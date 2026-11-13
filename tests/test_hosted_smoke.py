import unittest


class HostedSmokeTest(unittest.TestCase):
    def test_controlled_failure(self):
        self.fail("Intentional failure for the issue automation smoke test")
