<div align="center">
  
# 🔔 AI Reminders System — ClickUp v4.0

*A smart, context-aware notification and reminder system for teams using ClickUp.*

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](https://opensource.org/licenses/MIT)
[![ClickUp API](https://img.shields.io/badge/ClickUp-API-7B68EE.svg?style=flat-square&logo=clickup&logoColor=white)](https://clickup.com/api)
[![Twilio](https://img.shields.io/badge/Twilio-Voice_&_SMS-F22F46.svg?style=flat-square&logo=twilio&logoColor=white)](https://www.twilio.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-Whisper_&_GPT4-412991.svg?style=flat-square&logo=openai&logoColor=white)](https://openai.com/)

[Quick Start](docs/QUICKSTART_v4.md) • 
[Telegram Setup](TELEGRAM_SETUP.md) • 
[Webhook Guide](docs/REALTIME_TRANSCRIPTION_SETUP.md) • 
[Migration](docs/MIGRATION_COMPLETE_v4.md)

</div>

---

## 🌟 Overview

The **AI Reminders System** is an automated bot designed to fetch tasks from a specific ClickUp list and notify assignees via their preferred communication channel. It supports interactive Telegram buttons, voice calls via Twilio with AI-driven response transcription (OpenAI Whisper/GPT-4), and fallback SMS notifications.

Forget manual follow-ups—let the AI ask your team about task statuses and update ClickUp automatically!

## ✨ Key Features

- **🚀 Telegram Bot Engine (New)**: Interactive inline buttons (`✅ Done`, `❌ Blocked`, `🔄 In Progress`). Instantly updates the ClickUp task status and leaves a comment.
- **📞 AI Voice Calls**: Uses Twilio to call the assignee, dictate the task using TTS, record their response, and transcribe it using OpenAI Whisper.
- **🧠 Smart Status Parsing**: Analyzes conversational replies using GPT-4 to determine the task outcome.
- **🔀 Intelligent Routing**: Route reminders via multiple channels (`telegram`, `twilio_voice`, `twilio_sms`) tailored to individual assignees.
- **⚡ Instant Sync**: Automatic webhook-based updates sync the parsed status directly back to the assignee's ClickUp task.

## 🧭 Architecture Flow

1. **Trigger**: A cron job (or GitHub Action) runs `scripts/run_workflow_local.py`.
2. **Fetch**: The system queries the configured ClickUp Workspace & List for tasks matching the criteria.
3. **Dispatch**: 
    - **Telegram**: Sends an actionable message to the assignee.
    - **Voice (Fallback/Selected)**: Twilio initiates a call -> TwiML handles the voice interaction -> user responds.
4. **Process**: The Webhook Server receives the voice recording -> OpenAI transcribes -> GPT evaluates the status.
5. **Update**: ClickUp task status is modified, and a comment is added with the context.

---

## 🚀 Getting Started

### Prerequisites

- **Python 3.8+** (Recommended 3.10+)
- API Keys for: **ClickUp**, **Twilio**, **OpenAI**, and **Telegram Bot**
- A public HTTPS endpoint for Twilio Webhooks (via `ngrok` or similar for local testing)

### Installation

Clone the repository and install dependencies:

```bash
git clone https://github.com/danyanovich/clickup_reminders.git
cd clickup_reminders

# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install requirements
pip install -r requirements.txt
pip install Flask pytz  # Additional requirements for the webhook server
```

### Configuration

Copy the example configuration files and populate them with your keys.

```bash
cp config.example.json config.json
cp secrets.example.json secrets.json
```

Add your API Keys to the environment or the `secrets.json` file. The application gracefully supports `.env` structures. 

```json
// secrets.json (Example)
{
  "clickup": {
    "api_key": "your_clickup_api_key",
    "team_id": "your_clickup_team_id"
  },
  "telegram": {
    "bot_token": "your_telegram_bot_token"
  }
}
```

Edit `config.json` to map ClickUp assignees to their respective Telegram IDs or phone numbers.

### Running the System

#### 💬 Telegram Bot (Polling Mode)
Run the Telegram bot to handle incoming commands and inline button callbacks continuously:
```bash
python3 telegram_bot.py --initial-send --verbose
```

#### 📞 Twilio Webhook Server
Start the Flask server to receive Webhooks for voice interactions:
```bash
export WEBHOOK_PORT=5000
./start_webhook_server.sh start
```

#### 🔄 Manual / Cron Execution
Trigger a manual sweep of tasks and dispatch reminders:
```bash
python3 scripts/run_workflow_local.py --verbose
```

---

## 🧪 Testing

You can use the built-in testing scripts to verify your configuration before pushing to production:

```bash
# Verify Webhook Health & TwiML Generation
python3 test_realtime_transcription.py --health
python3 test_realtime_transcription.py --twiml

# Dry-run voice calls to check routing logic
python3 send_twilio_calls.py --dry-run
```

---

## 📁 Repository Structure

```text
.
├── config.json/secrets.json         # Configuration and Secrets (Ignored in Git)
├── clickup.py                       # ClickUp API Client
├── telegram_bot.py                  # Polling Telegram Bot Entrypoint
├── webhook_server.py                # Flask Webhook for Twilio Voice
├── telephony.py                     # Twilio Integration Logic
├── reminder_system.py               # Core dispatcher and scheduling logic
├── analysis.py                      # OpenAI NLP parsing logic
└── tests/                           # Unit and Integration tests
```

---

## 🔒 Security Best Practices

- **Never commit `secrets.json` or `.env` files.** They are ignored by default.
- In production, always use **HTTPS** for your Twilio Webhook endpoints.
- If exposing the Flask webhook server to the internet, put it behind a reverse proxy like **Nginx** and use TLS.
- Validate incoming Twilio webhook signatures in production to prevent spoofed HTTP requests.

---

## 🤝 Contributing

Contributions are welcome! If you'd like to improve the NLP logic, add new integration channels (like Slack or Discord), or optimize the webhook server:

1. Fork the repository
2. Create a new Feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 📄 License

Distributed under the **MIT License**. See [`LICENSE`](LICENSE) for more information.
