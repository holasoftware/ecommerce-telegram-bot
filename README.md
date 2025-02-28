# Ecommerce telegram bot
Reusable telegram bot to allow the user to buy products from different e-commerce engines.

## Features
- integration with telgram payments
- product carousel
- product image gallery
- checkout conversational scene
- cart conversational scene
- product recommendations using a large language model
- easy navigation through product categories

## Installation
Create a virtualenv and activate it:
```
    python -m venv env
    . env/bin/activate
```

Install the requirements:
```
    pip install -r requirements.txt
```

## Quick start
Add this environment variable `BOT_TELEGRAM_TOKEN` with your token.

Run the following command to start the telegram bot with a demo e-commerce:
```
    python ecommerce_telegram_bot.py
```