from .config import PRIORITIES, SEVERITIES, STATUSES, TRACKS
from .github import log


PAGE = "pageInfo { hasNextPage endCursor }"
PROJECT = "id number title url closed viewerCanUpdate"
FIELD = """
... on ProjectV2FieldCommon { id name dataType }
... on ProjectV2SingleSelectField { options { id name color description } }
... on ProjectV2IterationField {
  configuration {
    duration
    iterations { id title startDate duration }
    completedIterations { id title startDate duration }
  }
}
"""
VALUES = """
... on ProjectV2ItemFieldSingleSelectValue { optionId name field { ... on ProjectV2FieldCommon { name } } }
... on ProjectV2ItemFieldNumberValue { number field { ... on ProjectV2FieldCommon { name } } }
... on ProjectV2ItemFieldTextValue { text field { ... on ProjectV2FieldCommon { name } } }
... on ProjectV2ItemFieldDateValue { date field { ... on ProjectV2FieldCommon { name } } }
... on ProjectV2ItemFieldIterationValue { iterationId title startDate field { ... on ProjectV2FieldCommon { name } } }
"""
ITEM = """
id isArchived
content { ... on Issue { number repository { nameWithOwner } } }
fieldValues(first: 30) { nodes { """ + VALUES + " } }"
VIEW = "id name layout filter"
SCHEMA = {
    "Status": ("SINGLE_SELECT", STATUSES),
    "Priority": ("SINGLE_SELECT", PRIORITIES),
    "Story Points": ("NUMBER", ()),
    "Sprint": ("ITERATION", ()),
    "Epic": ("TEXT", ()),
    "Track": ("SINGLE_SELECT", TRACKS),
    "Due Date": ("DATE", ()),
    "Severity": ("SINGLE_SELECT", SEVERITIES),
}
COLORS = {
    "Ready": "BLUE", "In Progress": "BLUE", "In Review": "PURPLE", "QA/UAT": "YELLOW",
    "Done": "GREEN", "Blocked": "RED", "Critical": "RED", "High": "ORANGE",
    "Medium": "YELLOW", "Low": "BLUE", "UX/UI": "PURPLE", "Security": "RED",
}


def item_values(item):
    return {node["field"]["name"]: node for node in item["fieldValues"]["nodes"]
            if node and node.get("field")}


class GitHubProjectAdapter:
    def __init__(self, github, settings, token, writes):
        self.github, self.settings, self.token, self.writes = github, settings, token, writes
        self.project = None
        self._fields = None
        self._items = None

    def _query(self, document, variables, operation):
        return self.github.query(document, variables, token=self.token, operation=operation)

    def _connection(self, document, variables, extract, operation):
        return list(self.github.connection(document, variables, extract, token=self.token,
                                           operation=operation))

    def _mutate(self, name, values, result):
        if not self.writes:
            return None
        document = f"mutation($input: {name[0].upper() + name[1:]}Input!) {{ {name}(input: $input) {{ {result} }} }}"
        return self._query(document, {"input": values}, name)[name]

    def locate(self, *, create=False):
        repo = self.github.rest()
        account_type = "organization" if repo["owner"]["type"] == "Organization" else "user"
        owner = repo["owner"]["login"]
        number = self.settings.project_number
        if number:
            document = f"query($owner: String!, $number: Int!) {{ {account_type}(login: $owner) {{ projectV2(number: $number) {{ {PROJECT} }} }} }}"
            self.project = self._query(document, {"owner": owner, "number": number},
                                       "project lookup")[account_type]["projectV2"]
            if not self.project:
                raise ValueError("Configured Project was not found")
        else:
            document = (
                f"query($owner: String!, $cursor: String) {{ {account_type}(login: $owner) {{ "
                f"projectsV2(first: 50, after: $cursor) {{ {PAGE} nodes {{ {PROJECT} }} }} }} }}"
            )
            projects = self._connection(document, {"owner": owner},
                                        lambda data: data[account_type]["projectsV2"], "list Projects")
            matches = [item for item in projects if item["title"] == self.settings.project["title"] and not item["closed"]]
            if len(matches) > 1:
                raise ValueError("Set ENGINEERING_PROJECT_NUMBER to disambiguate Projects")
            self.project = next(iter(matches), None)
        if not self.project and create:
            title = self.settings.project["title"]
            log("project_create", dry_run=not self.writes)
            result = self._mutate("createProjectV2", {"ownerId": repo["owner"]["node_id"],
                                  "repositoryId": repo["node_id"], "title": title},
                                  f"projectV2 {{ {PROJECT} }}")
            self.project = result["projectV2"] if result else None
        if self.project:
            if self.project["closed"] or (self.writes and not self.project["viewerCanUpdate"]):
                raise ValueError("Project must be open and writable")
            log("project_selected", number=self.project["number"], url=self.project["url"])
        return self.project

    def _project_nodes(self, field, node, *, refresh=None):
        document = (
            f"query($id: ID!, $cursor: String) {{ node(id: $id) {{ ... on ProjectV2 "
            f"{{ {field}(first: 50, after: $cursor) {{ {PAGE} nodes {{ {node} }} }} }} }} }}"
        )
        return self._connection(document, {"id": self.project["id"]},
                                lambda data: data["node"][refresh or field], field)

    @property
    def fields(self):
        if self._fields is None:
            self._fields = {item["name"]: item for item in self._project_nodes("fields", FIELD)}
        return self._fields

    @property
    def items(self):
        if self._items is None:
            self._items = {
                item["content"]["number"]: item for item in self._project_nodes("items", ITEM)
                if item["content"] and item["content"].get("repository", {}).get("nameWithOwner", "").lower()
                == self.github.repository.lower() and not item["isArchived"]
            }
        return self._items

    def ensure_iterations(self, calendar, today):
        field = self.fields.get("Sprint")
        if not field:
            return
        config = field["configuration"]
        existing = sorted(config["completedIterations"] + config["iterations"], key=lambda item: item["startDate"])
        start = (calendar.current(today) or calendar.at(0)).number
        missing = [calendar.at(index) for index in range(start, start + calendar.horizon)
                   if calendar.at(index).start.isoformat() not in {item["startDate"] for item in existing}]
        if not missing:
            return
        if any(item["duration"] != calendar.days for item in existing):
            raise ValueError("Review the existing Sprint field before changing iteration duration")
        iterations = sorted(existing + [sprint.project_value() for sprint in missing], key=lambda item: item["startDate"])
        log("project_iterations_extend", count=len(missing), dry_run=not self.writes)
        result = self._mutate("updateProjectV2Field", {
            "fieldId": field["id"], "iterationConfiguration": {
                "duration": calendar.days, "startDate": iterations[0]["startDate"], "iterations": iterations,
            }
        }, f"projectV2Field {{ {FIELD} }}")
        if result:
            self.fields["Sprint"] = result["projectV2Field"]

    def configure(self, calendar, today):
        if not self.locate(create=True):
            for name, (kind, _) in SCHEMA.items():
                log("project_field_create", field=name, kind=kind, dry_run=True)
            for view in self.settings.project["views"]:
                log("project_view_create", view=view["name"], dry_run=True)
            return
        repo = self.github.rest()
        linked = self._project_nodes("repositories", "id")
        if repo["node_id"] not in {item["id"] for item in linked}:
            log("project_repository_link", dry_run=not self.writes)
            self._mutate("linkProjectV2ToRepository", {"projectId": self.project["id"],
                         "repositoryId": repo["node_id"]}, "clientMutationId")
        for name, (kind, options) in SCHEMA.items():
            field = self.fields.get(name)
            if field and field["dataType"] != kind:
                raise ValueError(f"Review existing Project field {name}: expected {kind}")
            values = {"name": name}
            if options:
                present = {item["name"]: item for item in field.get("options", [])} if field else {}
                if set(options) - present.keys():
                    values["singleSelectOptions"] = [
                        present.get(option) or {"name": option, "color": COLORS.get(option, "GRAY"), "description": ""}
                        for option in dict.fromkeys((*options, *present))
                    ]
            if not field:
                log("project_field_create", field=name, kind=kind, dry_run=not self.writes)
                values.update(projectId=self.project["id"], dataType=kind)
                if kind == "ITERATION":
                    start = (calendar.current(today) or calendar.at(0)).number
                    indices = sorted({0, 1, *range(start, start + calendar.horizon)})
                    values["iterationConfiguration"] = {
                        "duration": calendar.days, "startDate": calendar.at(0).start.isoformat(),
                        "iterations": [calendar.at(index).project_value() for index in indices],
                    }
                result = self._mutate("createProjectV2Field", values, f"projectV2Field {{ {FIELD} }}")
            elif "singleSelectOptions" in values:
                log("project_field_extend", field=name, dry_run=not self.writes)
                result = self._mutate("updateProjectV2Field", {**values, "fieldId": field["id"]},
                                      f"projectV2Field {{ {FIELD} }}")
            else:
                continue
            if result:
                self.fields[name] = result["projectV2Field"]
        self.ensure_iterations(calendar, today)
        views = {view["name"]: view for view in self._project_nodes("views", VIEW)}
        visible = ["Title", "Status", "Priority", "Story Points", "Sprint", "Track", "Assignees", "Due Date", "Milestone"]
        for spec in self.settings.project["views"]:
            name, layout, filter_value = spec["name"], spec["layout"], spec["filter"]
            view = views.get(name)
            if not view:
                log("project_view_create", view=name, dry_run=not self.writes)
                result = self._mutate("createProjectV2View", {
                    "projectId": self.project["id"], "name": name, "layout": layout,
                    "configuration": {"visibleFieldIds": [self.fields[n]["id"] for n in visible if n in self.fields]},
                }, f"projectV2View {{ {VIEW} }}")
                view = result["projectV2View"] if result else None
            if view and (view["layout"] != layout or (view.get("filter") or "") != filter_value):
                log("project_view_update", view=name, dry_run=not self.writes)
                self._mutate("updateProjectV2View", {"viewId": view["id"], "layout": layout,
                             "filter": filter_value}, "clientMutationId")

    def ensure_item(self, issue):
        number = issue["number"]
        item = self.items.get(number)
        if item:
            return item
        log("project_item_add", issue=number, dry_run=not self.writes)
        result = self._mutate("addProjectV2ItemById",
                              {"projectId": self.project["id"], "contentId": issue["node_id"]},
                              f"item {{ {ITEM} }}")
        if result:
            self.items[number] = result["item"]
        return result["item"] if result else None

    def get(self, number, name, attribute):
        item = self.items.get(number)
        return item_values(item).get(name, {}).get(attribute) if item else None

    def set(self, issue, name, value, *, initial=False):
        field = self.fields.get(name)
        if not field:
            raise ValueError(f"Project is missing the {name} field. Run setup")
        kind = field["dataType"]
        attribute = {"SINGLE_SELECT": "optionId", "TEXT": "text", "NUMBER": "number",
                     "DATE": "date", "ITERATION": "iterationId"}[kind]
        if kind == "SINGLE_SELECT":
            value = next((item["id"] for item in field["options"] if item["name"] == value), None)
        elif kind == "ITERATION":
            config = field["configuration"]
            value = next((item["id"] for item in config["completedIterations"] + config["iterations"]
                          if item["startDate"] == value), None)
        if value is None:
            raise ValueError(f"Project {name} option was not found. Run setup")
        item = self.ensure_item(issue)
        existing = self.get(issue["number"], name, attribute) if item else None
        if existing == value or (initial and existing is not None):
            return
        log("project_field_update", issue=issue["number"], field=name, dry_run=not self.writes)
        if item:
            result = self._mutate("updateProjectV2ItemFieldValue", {
                "projectId": self.project["id"], "itemId": item["id"], "fieldId": field["id"],
                "value": {"singleSelectOptionId" if kind == "SINGLE_SELECT" else attribute: value},
            }, f"projectV2Item {{ {ITEM} }}")
            if result:
                self.items[issue["number"]] = result["projectV2Item"]
