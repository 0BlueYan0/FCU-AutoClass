"""This python will handle some extra functions."""
import json
import logging
import sys
import urllib.request
from os.path import exists

import ddddocr
import yaml
from yaml import SafeLoader


def config_file_generator():
    """Generate the template of config file"""
    with open('config.yml', 'w', encoding="utf8") as f:
        f.write("""# ++--------------------------------++
# | FCU-AutoClass                    |
# | Made by LD (MIT License)         |
# ++--------------------------------++

# FCU Account
username: ''
password: ''

# Class to join
# If you have more than one class to join, please separate them with space.
# Example: class_id: '0050 0051'
# The less class_id you have, the more rate you can get the class you want.
class_id: ''

# Headless mode
# If you want to run this script in headless mode, please set this to true.
headless: false

# Discord notification
# Paste your Discord webhook URL here to get notified when a class is joined.
# Leave it empty ('') to disable notifications.
discord_webhook_url: ''
"""
                )
    sys.exit()


def read_config():
    """Read config file.

    Check if config file exists, if not, create one.
    if exists, read config file and return config with dict type.

    :rtype: dict
    """
    if not exists('./config.yml'):
        logging.warning(
            "Config file not found, create one by default.\nPlease finish filling config.yml")
        with open('config.yml', 'w', encoding="utf8"):
            config_file_generator()

    try:
        with open('config.yml', 'r', encoding="utf8") as f:
            data = yaml.load(f, Loader=SafeLoader)
            class_ids = get_class_ids(data['class_id'])
            config = {
                'username': data['username'],
                'password': data['password'],
                'class_ids': class_ids,
                'headless': data['headless'],
                # Optional key, so old config files without it keep working.
                'discord_webhook_url': str(data.get('discord_webhook_url') or '').strip()
            }
            return config
    except (KeyError, TypeError):
        logging.error(
            "An error occurred while reading config.yml, please check if the file is corrected filled.\n"
            "If the problem can't be solved, consider delete config.yml and restart the program.\n")
        sys.exit()


def get_class_ids(class_id):
    """Read class_id from config file.

    :rtype: list
    """
    class_ids = class_id.split(" ")
    return class_ids


def get_ocr_answer(ocr_image_path):
    """Get the answer of ocr.

    :rtype: str
    """
    ocr = ddddocr.DdddOcr()
    with open(ocr_image_path, 'rb') as f:
        image = f.read()
    answer = ocr.classification(image)
    return answer


def send_discord_notification(webhook_url, message):
    """Send a message to a Discord webhook.

    Never raises: a failed notification is only logged, so it can never
    interrupt the auto class loop.

    :param webhook_url: Discord webhook URL. Empty string disables sending.
    :param message: Message content to send.
    """
    if not webhook_url:
        return
    payload = json.dumps({'content': message}).encode('utf-8')
    try:
        request = urllib.request.Request(
            webhook_url, data=payload, method='POST',
            headers={'Content-Type': 'application/json',
                     'User-Agent': 'FCU-AutoClass'})
        with urllib.request.urlopen(request, timeout=10):
            pass
    # URLError, HTTPError and socket timeouts are all OSError subclasses;
    # ValueError is raised by Request() for a malformed URL.
    except (OSError, ValueError) as error:
        logging.warning("Discord 通知發送失敗: %s", error)
