#!/usr/bin/env python3
"""
Automated Reminder System - Main Script
Checks ClickUp for due reminders, makes calls via Twilio, analyzes responses with OpenAI
"""

import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import time
import requests
from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Gather
import openai

try:
    from openai import OpenAI  # type: ignore
except ImportError:  # pragma: no cover - compatibility shim
    OpenAI = None  # type: ignore

# Load configuration
DEFAULT_CONFIG_PATH = "/home/ubuntu/reminder_system/config.json"
DEFAULT_SECRETS_PATH = "/home/ubuntu/.config/abacusai_auth_secrets.json"
DEFAULT_CALL_LOG_PATH = "/home/ubuntu/reminder_system/call_logs/"
DEFAULT_DELIVERABLES_PATH = "/home/ubuntu/reminder_system/reports/"

CONFIG_PATH = Path(os.getenv("CONFIG_PATH", DEFAULT_CONFIG_PATH))
SECRETS_PATH = Path(os.getenv("SECRETS_PATH", DEFAULT_SECRETS_PATH))
CALL_LOG_PATH = Path(os.getenv("CALL_LOG_PATH", DEFAULT_CALL_LOG_PATH))
DELIVERABLES_PATH = Path(os.getenv("DELIVERABLES_PATH", DEFAULT_DELIVERABLES_PATH))

ENV_SECRET_KEYS = {
    'clickup_api_key': 'CLICKUP_API_KEY',
    'clickup_team_id': 'CLICKUP_TEAM_ID',
    'twilio_sid': 'TWILIO_ACCOUNT_SID',
    'twilio_token': 'TWILIO_AUTH_TOKEN',
    'twilio_phone': 'TWILIO_PHONE_NUMBER',
    'openai_api_key': 'OPENAI_API_KEY'
}

def load_config():
    """Load configuration from JSON file"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def load_secrets():
    """Load API secrets"""
    env_values = {
        key: os.getenv(env_name)
        for key, env_name in ENV_SECRET_KEYS.items()
    }

    if all(env_values.values()):
        return env_values

    if not SECRETS_PATH.exists():
        missing_envs = [name for key, name in ENV_SECRET_KEYS.items() if not env_values[key]]
        raise FileNotFoundError(
            f"Secrets file not found at {SECRETS_PATH} and missing environment variables: {', '.join(missing_envs)}"
        )

    with open(SECRETS_PATH, 'r', encoding='utf-8') as f:
        secrets = json.load(f)

    return {
        'clickup_api_key': secrets['telegram']['secrets']['clickup_api_key']['value'],
        'clickup_team_id': secrets['telegram']['secrets']['clickup_team_id']['value'],
        'twilio_sid': secrets['twilio']['secrets']['account_sid']['value'],
        'twilio_token': secrets['twilio']['secrets']['auth_token']['value'],
        'twilio_phone': secrets['twilio']['secrets']['phone_number']['value'],
        'openai_api_key': secrets['openai']['secrets']['api_key']['value']
    }

def get_clickup_tasks(api_key, team_id, list_name):
    """Fetch tasks from ClickUp Reminders list"""
    headers = {
        'Authorization': api_key,
        'Content-Type': 'application/json'
    }
    
    # Get workspace hierarchy to find the list
    hierarchy_url = f'https://api.clickup.com/api/v2/team/{team_id}/space'
    response = requests.get(hierarchy_url, headers=headers)
    response.raise_for_status()
    spaces = response.json()['spaces']
    
    # Find the Reminders list
    list_id = None
    for space in spaces:
        space_id = space['id']
        lists_url = f'https://api.clickup.com/api/v2/space/{space_id}/list'
        lists_response = requests.get(lists_url, headers=headers)
        lists_response.raise_for_status()
        
        for lst in lists_response.json()['lists']:
            if lst['name'] == list_name:
                list_id = lst['id']
                break
        if list_id:
            break
    
    if not list_id:
        print(f"List '{list_name}' not found")
        return []
    
    # Get tasks from the list
    tasks_url = f'https://api.clickup.com/api/v2/list/{list_id}/task'
    tasks_response = requests.get(tasks_url, headers=headers)
    tasks_response.raise_for_status()
    
    return tasks_response.json()['tasks']

def is_task_due(task, current_time):
    """Check if task is due for reminder"""
    if not task.get('due_date'):
        return False
    
    due_timestamp = int(task['due_date']) / 1000  # Convert from milliseconds
    due_time = datetime.fromtimestamp(due_timestamp)
    
    # Check if due time is within the next 30 minutes
    time_diff = (due_time - current_time).total_seconds() / 60
    return 0 <= time_diff <= 30

def extract_recipient_name(task_name):
    """Extract recipient name from task title"""
    # Assuming format: "Reminder: [Name] - [Task description]"
    if ':' in task_name:
        parts = task_name.split(':')[1].strip()
        if '-' in parts:
            return parts.split('-')[0].strip()
        return parts.strip()
    return None

def make_call(twilio_client, from_phone, to_phone, tasks_descriptions):
    """Initiate call via Twilio with multiple tasks"""
    try:
        # Create TwiML for the call
        twiml = VoiceResponse()
        gather = Gather(
            input='speech',
            timeout=10,
            language='ru-RU',
            speech_timeout='auto',
            action='/voice-response'  # This would need a webhook endpoint
        )
        
        # Build message with all tasks
        if len(tasks_descriptions) == 1:
            message = f"Здравствуйте! Напоминание о задаче: {tasks_descriptions[0]}. Что выполнили?"
        else:
            tasks_list = ". ".join([f"{i+1}. {desc}" for i, desc in enumerate(tasks_descriptions)])
            message = f"Здравствуйте! У вас {len(tasks_descriptions)} напоминания: {tasks_list}. Что выполнили?"
        
        gather.say(message, language='ru-RU')
        twiml.append(gather)
        
        call = twilio_client.calls.create(
            to=to_phone,
            from_=from_phone,
            twiml=str(twiml),
            record=True,
            recording_status_callback_event=['completed']
        )
        
        return {
            'success': True,
            'call_sid': call.sid,
            'status': call.status
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def send_sms(twilio_client, from_phone, to_phone, message):
    """Send SMS via Twilio"""
    try:
        sms = twilio_client.messages.create(
            to=to_phone,
            from_=from_phone,
            body=message
        )
        return {
            'success': True,
            'message_sid': sms.sid
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }

def analyze_voice_response(openai_api_key, recording_url):
    """Analyze voice response using OpenAI Whisper and ChatGPT"""
    client = OpenAI(api_key=openai_api_key) if OpenAI else None

    try:
        # Download recording
        response = requests.get(recording_url)
        audio_path = '/tmp/recording.mp3'
        with open(audio_path, 'wb') as f:
            f.write(response.content)
        
        # Transcribe with Whisper
        with open(audio_path, 'rb') as audio_file:
            if client:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="ru"
                )
                transcribed_text = transcript.text
            else:
                try:
                    openai.api_key = openai_api_key
                    transcript = openai.audio.transcriptions.create(
                        model="whisper-1",
                        file=audio_file,
                        language="ru"
                    )
                    transcribed_text = transcript.text
                except AttributeError:
                    transcript = openai.Audio.transcribe(  # type: ignore[attr-defined]
                        model="whisper-1",
                        file=audio_file,
                        language="ru"
                    )
                    transcribed_text = transcript["text"]
        
        # Analyze with ChatGPT
        messages = [
            {
                "role": "system",
                "content": "Ты анализируешь ответы на напоминания о задачах. Определи, выполнена ли задача, и верни JSON с полями: completed (true/false), confidence (0-1), summary (краткое описание ответа)."
            },
            {
                "role": "user",
                "content": f"Ответ пользователя: {transcribed_text}"
            }
        ]

        if client:
            analysis = client.chat.completions.create(
                model="gpt-4",
                messages=messages,
                response_format={"type": "json_object"}
            )
            analysis_content = analysis.choices[0].message.content
        else:
            try:
                analysis = openai.chat.completions.create(  # type: ignore[attr-defined]
                    model="gpt-4",
                    messages=messages,
                    response_format={"type": "json_object"}
                )
                analysis_content = analysis.choices[0].message.content
            except AttributeError:
                analysis = openai.ChatCompletion.create(
                    model="gpt-4",
                    messages=messages
                )
                analysis_content = analysis['choices'][0]['message']['content']

        result = json.loads(analysis_content)
        result['transcript'] = transcribed_text
        return result
        
    except Exception as e:
        return {
            'completed': False,
            'confidence': 0,
            'summary': f'Error analyzing response: {str(e)}',
            'transcript': ''
        }

def update_task_status(api_key, task_id, status, comment=None):
    """Update task status in ClickUp"""
    headers = {
        'Authorization': api_key,
        'Content-Type': 'application/json'
    }
    
    # Update status
    status_url = f'https://api.clickup.com/api/v2/task/{task_id}'
    status_data = {'status': status}
    response = requests.put(status_url, headers=headers, json=status_data)
    response.raise_for_status()
    
    # Add comment if provided
    if comment:
        comment_url = f'https://api.clickup.com/api/v2/task/{task_id}/comment'
        comment_data = {'comment_text': comment}
        requests.post(comment_url, headers=headers, json=comment_data)

def log_call(task_id, task_name, recipient, phone, call_result, analysis_result=None):
    """Log call details to file"""
    CALL_LOG_PATH.mkdir(parents=True, exist_ok=True)
    
    log_entry = {
        'timestamp': datetime.now().isoformat(),
        'task_id': task_id,
        'task_name': task_name,
        'recipient': recipient,
        'phone': phone,
        'call_result': call_result,
        'analysis_result': analysis_result
    }
    
    log_file = CALL_LOG_PATH / f"call_log_{datetime.now().strftime('%Y%m%d')}.json"
    
    logs = []
    if log_file.exists():
        with open(log_file, 'r', encoding='utf-8') as f:
            logs = json.load(f)
    
    logs.append(log_entry)
    
    with open(log_file, 'w', encoding='utf-8') as f:
        json.dump(logs, f, indent=2, ensure_ascii=False)

def generate_report(processed_tasks):
    """Generate daily report"""
    DELIVERABLES_PATH.mkdir(parents=True, exist_ok=True)
    
    report_date = datetime.now().strftime('%Y-%m-%d')
    report_path = DELIVERABLES_PATH / f"reminder_report_{report_date}.md"
    
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(f"# Отчет по напоминаниям - {report_date}\n\n")
        f.write(f"**Время выполнения:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write(f"**Всего обработано задач:** {len(processed_tasks)}\n\n")
        
        if processed_tasks:
            f.write("## Детали обработанных задач\n\n")
            for task in processed_tasks:
                f.write(f"### {task['task_name']}\n\n")
                f.write(f"- **Получатель:** {task['recipient']}\n")
                f.write(f"- **Телефон:** {task['phone']}\n")
                f.write(f"- **Статус звонка:** {'✅ Успешно' if task['call_success'] else '❌ Ошибка'}\n")
                
                if task.get('analysis'):
                    f.write(f"- **Задача выполнена:** {'✅ Да' if task['analysis']['completed'] else '❌ Нет'}\n")
                    f.write(f"- **Уверенность:** {task['analysis']['confidence']*100:.0f}%\n")
                    f.write(f"- **Транскрипция:** {task['analysis'].get('transcript', 'N/A')}\n")
                    f.write(f"- **Резюме:** {task['analysis']['summary']}\n")
                
                f.write("\n")
        else:
            f.write("*Нет задач для обработки в этом интервале.*\n")
    
    return report_path

def main():
    """Main execution function"""
    print(f"[{datetime.now()}] Starting reminder system check...")
    
    # Load configuration and secrets
    config = load_config()
    secrets = load_secrets()
    
    # Initialize clients
    twilio_client = Client(secrets['twilio_sid'], secrets['twilio_token'])
    
    # Get current time
    current_time = datetime.now()
    
    # Check if within working hours
    if not (config['working_hours']['start'] <= current_time.hour < config['working_hours']['end']):
        print(f"Outside working hours ({config['working_hours']['start']}:00 - {config['working_hours']['end']}:00)")
        return
    
    # Fetch tasks from ClickUp
    tasks = get_clickup_tasks(
        secrets['clickup_api_key'],
        secrets['clickup_team_id'],
        config['reminder_list_name']
    )
    
    print(f"Found {len(tasks)} tasks in '{config['reminder_list_name']}' list")
    
    # Collect all due tasks for Alex
    alex_tasks = []
    alex_phone = config['phone_mapping'].get('Alex')
    
    if not alex_phone:
        print(f"⚠️ Номер телефона для Alex не найден в конфигурации")
        return
    
    for task in tasks:
        if not is_task_due(task, current_time):
            continue
        
        print(f"Processing task: {task['name']}")
        
        # Get task description
        task_description = task.get('description', '').strip()
        if not task_description:
            if '-' in task['name']:
                task_description = task['name'].split('-', 1)[1].strip()
            else:
                task_description = task['name']
        
        # Clean description: replace newlines with spaces for voice
        task_description = ' '.join(task_description.split())
        
        alex_tasks.append({
            'task': task,
            'description': task_description
        })
    
    if not alex_tasks:
        print(f"\n✅ Нет задач для обработки")
        return
    
    # Make ONE call with ALL tasks
    print(f"\n📞 Звоню Алексу с {len(alex_tasks)} напоминаниями...")
    
    task_descriptions = [t['description'] for t in alex_tasks]
    call_result = make_call(
        twilio_client,
        secrets['twilio_phone'],
        alex_phone,
        task_descriptions
    )
    
    processed_tasks = []
    
    if call_result:
        print(f"✅ Звонок выполнен: {call_result['status']}")
        
        if call_result['success']:
            print(f"  ✅ Call initiated: {call_result['call_sid']}")
            
            # Wait for call to complete and get recording
            time.sleep(35)  # Wait for call timeout + processing
            
            # Update all tasks
            for task_info in alex_tasks:
                task = task_info['task']
                task_description = task_info['description']
                
                update_task_status(
                    secrets['clickup_api_key'],
                    task['id'],
                    'in progress',
                    f"Звонок выполнен {datetime.now().strftime('%Y-%m-%d %H:%M')}. Ожидание ответа."
                )
                
                task_log = {
                    'task_id': task['id'],
                    'task_name': task['name'],
                    'recipient': 'Alex',
                    'phone': alex_phone,
                    'call_success': True,
                    'analysis': {
                        'completed': False,
                        'confidence': 0,
                        'summary': 'Звонок выполнен, ожидание анализа записи',
                        'transcript': ''
                    }
                }
                processed_tasks.append(task_log)
        else:
            print(f"  ❌ Call failed: {call_result['error']}")
            
            # Send SMS as fallback with all tasks
            sms_message = "Напоминания:\n" + "\n".join([f"{i+1}. {t['description']}" for i, t in enumerate(alex_tasks)])
            sms_result = send_sms(
                twilio_client,
                secrets['twilio_phone'],
                alex_phone,
                sms_message
            )
            
            if sms_result['success']:
                print(f"  📱 SMS sent as fallback")
                
            for task_info in alex_tasks:
                task = task_info['task']
                update_task_status(
                    secrets['clickup_api_key'],
                    task['id'],
                    'in progress',
                    f"SMS отправлено {datetime.now().strftime('%Y-%m-%d %H:%M')} (звонок не удался)"
                )
        
        # Log the call for all tasks
        for task_info in alex_tasks:
            log_call(
                task_info['task']['id'],
                task_info['task']['name'],
                'Alex',
                alex_phone,
                call_result,
                None
            )
    
    # Generate report
    if processed_tasks:
        report_path = generate_report(processed_tasks)
        print(f"\n📊 Report generated: {report_path}")
    
    print(f"[{datetime.now()}] Reminder system check completed. Processed {len(processed_tasks)} tasks.")

if __name__ == "__main__":
    main()
