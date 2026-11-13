# Telegram setup

1. Open Telegram and ask the official BotFather for `/newbot`. Keep the issued token in a password manager.
2. Open the new bot's chat and send `/start`. For a group or channel, add the bot and grant the minimum permission to send messages.
3. Obtain the chat ID through the official Bot API. With the token available in the current process environment, run the following command in a private terminal. It prints only chat IDs.

```powershell
python -c "import json, os, urllib.request; t=os.environ['TELEGRAM_BOT_TOKEN']; r=json.load(urllib.request.urlopen('https://api.telegram.org/bot'+t+'/getUpdates',timeout=15)); print(sorted({m['chat']['id'] for u in r['result'] for k in ('message','channel_post','my_chat_member') if (m:=u.get(k))}))"
```

4. Add `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as repository Actions secrets. Clear the temporary environment value.
5. Dispatch Engineering operations with `test-notification` and Apply. Confirm receipt in the configured chat and look for `notification_delivered` in the job log.

The adapter uses plain text and direct GitHub links. Settings control event preferences and cooldowns. Set `notifications.enabled` to false to turn off Telegram while retaining GitHub reports. Change destinations by replacing the chat secret. See [Telegram Bot API](https://core.telegram.org/bots/api) and [Bot FAQ](https://core.telegram.org/bots/faq).
