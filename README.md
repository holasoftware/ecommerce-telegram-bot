# Ecommerce telegram bot
Reusable telegram bot to allow the user to buy products from different e-commerce engines.

## Features
- integration with telegram payments
- product carousel
- product image gallery
- checkout conversational scene
- cart conversational scene
- discounts
- product recommendations using a large language model
- easy navigation through product categories
- product search

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
Configure this environment variable `BOT_TELEGRAM_TOKEN` with your token.

Run the script to start the telegram bot with a demo e-commerce:
```
    python ecommerce_telegram_bot.py
```