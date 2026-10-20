# Six application worker bots

The existing `TELEGRAM_BOT_TOKEN` remains the main ApplyPilot bot. It handles discovery,
batch launches, global controls and progress after each ten confirmed applications.
Six optional bots accompany the same six application owners. They do not create
additional browser workers or application pipelines.

Create six bots in Telegram's BotFather with these suggested display names:

| Owner slot | Display name | Environment variable |
| --- | --- | --- |
| 1 | ApplyPilot Worker 1 | `TELEGRAM_WORKER_1_BOT_TOKEN` |
| 2 | ApplyPilot Worker 2 | `TELEGRAM_WORKER_2_BOT_TOKEN` |
| 3 | ApplyPilot Worker 3 | `TELEGRAM_WORKER_3_BOT_TOKEN` |
| 4 | ApplyPilot Worker 4 | `TELEGRAM_WORKER_4_BOT_TOKEN` |
| 5 | ApplyPilot Worker 5 | `TELEGRAM_WORKER_5_BOT_TOKEN` |
| 6 | ApplyPilot Worker 6 | `TELEGRAM_WORKER_6_BOT_TOKEN` |

BotFather requires a globally available username ending in `bot` for each bot.
Choose an available private project prefix, for example `yourprefix_applypilot_worker1_bot`.
Put each unique token in the corresponding variable in the private `.env` file,
retain the main token, and use the existing numeric `TELEGRAM_USER_ID` for all bots.
Send `/start` in a private chat with each bot, then restart the application to load
the configuration. Tokens must differ from each other and from the main bot.
Missing tokens disable only their corresponding companion; duplicate tokens are
reported and excluded from polling to prevent competing `getUpdates` consumers.

Each worker bot supports `/status`, `/job`, `/pause`, `/resume`, `/retry`, `/delete`,
`/query` and `/help`. Its controls affect only a job assigned to that owner slot.
Use the main bot for new links, batches and global settings. Questions and screenshots
arrive through the assigned companion when configured, with the main bot providing
delivery for slots without a companion. Only the configured owner's private chat
can control the application.

Reply to a question, screenshot or job card to preserve its exact job context.
An older message remains associated with its original job even after the slot takes
another job; a superseded question cannot change saved answers. Message IDs,
conversation contexts and update offsets are durable and scoped independently for
each bot. Plain answers are accepted only for the current job's own pending question.
Worker status reports token configuration and polling health without revealing tokens.

The six environment variables configure existing BotFather bots. The application
cannot create Telegram accounts or bots, and blank variables do not imply that bots
were created or connected.
