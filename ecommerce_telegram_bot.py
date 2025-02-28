import os
import logging
import random
import json
from decimal import Decimal
from dataclasses import dataclass
from enum import Enum, auto

# litellm is an optional dependency
try:
    import litellm
except ImportError:
    litellm = None

import telegram
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice, InputMediaPhoto
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, CallbackContext, PreCheckoutQueryHandler, ShippingQueryHandler, ConversationHandler


logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TELEGRAM_TOKEN")

# PAYMENT_PROVIDER_TOKEN: You need to replace this with your actual payment provider token (e.g., Stripe, Yandex.Checkout)
PAYMENT_PROVIDER_TOKEN = os.environ.get("BOT_TELEGRAM_PAYMENT_PROVIDER_TOKEN")
LANGUAGE_CODE = os.getenv("BOT_TELEGRAM_LANGUAGE_CODE")

translations = {
    "en": {

    }
}

def _(text):
    """Translate text according to the current language"""
    return translations[LANGUAGE_CODE].get(text, text) 


@dataclass
class CartItem:
    product_id: int
    quantity: int
    product_variant_id: int | None = None


@dataclass
class CartSession:
    user_id: int
    items: list[CartItem]

@dataclass
class ProductVariant:
    id: int
    title: str
    value: str
    stock: int

@dataclass
class ProductCategory:
    id: int
    name: str
    parent_product_id: int | None = None
    product_ids: list[int] | None = None
    subcategory_ids: list[int] | None = None


@dataclass
class Product:
    id: int
    name: str
    category_id: int
    description: str
    price: float
    discount: float | None = None
    variants: list[ProductVariant] | None = None
    image_urls: list[str] | None = None
    stock: int | None = None
    is_digital_product: bool = False


class ShoppingCart:
    """
    A class to represent a shopping cart for an e-commerce bot.
    Data is stored in memory (replace with a database in a real application).
    """

    def __init__(self, ecommerce, user_id):
        self.ecommerce = ecommerce
        self.user_id = user_id

    def get_items(self):
        raise NotImplementedError

    def add_item(self, product_id, quantity=1):
        """Adds a product to the user's cart."""
        raise NotImplementedError

    def remove_item(self, product_id, quantity=1):
        """Removes a product from the user's cart by its product ID."""
        raise NotImplementedError

    def remove_all_items_of_product(self, product_id):
        """Removes a product from the user's cart by its product ID."""
        raise NotImplementedError

    def clear(self):
        raise NotImplementedError

    def get_num_items(self):
        raise NotImplementedError

    def has_product_by_id(self, product_id):
        raise NotImplementedError

    def get_session_data(self):
        items = self.get_items()
        return CartSession(user_id=self.user_id, items=items)

    def is_empty(self):
        return len(self) == 0

    def has_items(self):
        return not self.is_empty()

    def calculate_total(self):
        """Calculates the total price of the items in the user's cart."""
        cart_items = self.get_items()
        total = Decimal()
        for item in cart_items:
            total += item.quantity * Decimal(item.price)
        return total

    def get_summary(self, summary_header="Your Cart:", cart_empty_text="Your cart is empty.", total_text="Total", currency_symbol="$", localized_price_template="{currency_symbol}{price:.2f}"):
        """Returns a string summary of the user's cart."""
        cart_items = self.get_items()
        if not cart_items:
            return cart_empty_text

        summary = summary_header + "\n"

        total_price = Decimal()
        for item in cart_items:
            price = item.price
            item_total = item.quantity * Decimal(price)

            localized_item_total = localized_price_template.format(currency_symbol=currency_symbol, price=item_total)

            total_price += item_total
            summary += f"- {item.name} x{item.quantity} = {localized_item_total}\n"

        localized_cart_total_price = localized_price_template.format(currency_symbol=currency_symbol, price=item_total)

        summary += "\n" + total_text + ": " + localized_cart_total_price
        return summary

    def __iter__(self):
        return self.get_items()
    
    def __len__(self):
        return self.get_num_items()

    def __contains__(self, product):
        if isinstance(product, Product):
            product_id = product.id
        elif isinstance(product, int):
            product_id = product
        else:
            raise ValueError("Not possible to use this object in the 'in' operator: %r" % product)

        return self.has_product_by_id(product_id)


class Ecommerce:
    """
    A class to represent the backend of an e-commerce engine.
    """

    shopping_cart_class = ShoppingCart
    currency = "USD"

    def browse_products(self, q=None, category_id=None, limit=None):
        raise NotImplementedError

    def get_all_products(self):
        return self.browse_products()

    def get_product_by_id(self, product_id):
        raise NotImplementedError

    def get_categories(self, parent_category_id=None):
        raise NotImplementedError

    def get_cart(self, user_id):
        raise self.shopping_cart_class(self, user_id)

    def get_currency(self):
        return self.currency


class ShoppingCartDemo(ShoppingCart):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._items = []

    def _find_item_by_product_id(self, product_id, variant_id=None):
        for i, item in enumerate(self._items):
            if item.product_id == product_id:
                return True, i, item

        return False, None, None

    def add_item(self, product_id, quantity=1, variant_id=None):
        found, item_index, item = self._find_item_by_product_id(product_id, variant_id=variant_id)

        if found:
            item.quantity += quantity
        else:
            item = CartItem(product_id=product_id, quantity=quantity, price=price, variant_id=variant_id)
            self._items.append(item)

        return item

    def remove_item(self, product_id, quantity=1, variant_id=None):
        found, item_index, item = self._find_item_by_product_id(product_id, variant_id=variant_id)

        if found:
            if item.quantity <= quantity:
                self._items = self._items[:item_index] + self._items[item_index+1:]
            else:
                item.quantity -= quantity

                if item.quantity != 0:
                    return item

    def remove_all_items_of_product(self, product_id):
        found, item_index, item = self._find_item_by_product_id(product_id, variant_id=variant_id)
        if found:
            self._items = self._items[:item_index] + self._items[item_index+1:]
            return True
        else:
            return False

    def clear(self):
        self._items = []

    def get_num_items(self):
        raise len(self._items)

    def has_product_by_id(self, product_id):
        found, _, _ = self._find_item_by_product_id(product_id)
        return found


class EcommerceDemo(Ecommerce):
    """
    Ecommerce for demo purposes. Data is stored in memory
    """
    shopping_cart_class = ShoppingCartDemo

    categories = [
        ProductCategory(name='Electronics', id=0, subcategory_ids=[4,5]),
        ProductCategory(name='Clothing', id=1, subcategory_ids=[6,7,8]),
        ProductCategory(name='Books', id=2, subcategory_ids=[9,10]),
        ProductCategory(name='Home & Kitchen', id=3),
        ProductCategory(name='Laptops', id=4, parent_product_id=0),
        ProductCategory(name='Smartphones', id=5, parent_product_id=0),
        ProductCategory(name='T-shirts', id=6, parent_product_id=1),
        ProductCategory(name='Jeans', id=7, parent_product_id=1),
        ProductCategory(name='Caps', id=8, parent_product_id=1),
        ProductCategory(name='Fiction', id=9, parent_product_id=2),
        ProductCategory(name='Non-fiction', id=10, parent_product_id=2)
    ]

    def __init__(self):
        self._carts = {}
        self._products = []
        self._products_in_category = []

        self._generate_demo_data()

    def _generate_demo_data(self):
        categories = self.categories

        products = self._products
        products_in_category = self._products_in_category

        for category in self.categories:
            category_id = category.id
            product_ids = []

            products_in_category.append(product_ids)

            for product_id in range(self._get_random_num_products_in_category()):
                product_id = len(products)
                product_ids.append(product_id)

                products.append(Product(
                    id=product_id,
                    category_id=category_id,
                    name='Product %d' % product_id,
                    image_urls=['https://via.placeholder.com/150', 'https://via.placeholder.com/200', 'https://via.placeholder.com/250'],
                    description='This is product %d.' % product_id,
                    price= round(random.uniform(1, 1000), 1)
                ))

    def _get_random_num_products_in_category(self):
        return random.randint(1,5)

    def get_product_by_id(self, product_id):
        return self.products[product_id]


class EcommerceTelegramBot:
    class EcommerceTelegramBotState(Enum):
        RECOMMENDATIONS = auto()
        SEARCH_IN_CURRENT_CATEGORY = auto()

    class ProductDetailImageViewType(Enum):
        IMAGE_GALLERY = auto()
        CAROUSEL = auto()


    def __init__(self, token, ecommerce, product_recommendations_enabled=False, payment_provider_token=None, llm_model="gpt-3.5-turbo", llm_temperature=0.7, language="en", payment_need_name=True, payment_need_shipping_address=True, payment_need_phone_number=True, payment_need_email=True, product_specification_separator="\n\n--------------------\n\n", product_detail_image_view_type=ProductDetailImageViewType.IMAGE_GALLERY):
        self.token = token
        self.llm_model = llm_model
        self.llm_temperature = llm_temperature

        if product_recommendations_enabled and litellm is None:
            logger.warn("Module 'litellm' is not installed. Not possible to use product recommendations feature")
            product_recommendations_enabled = False

        self.product_recommendations_enabled = product_recommendations_enabled
        self.payment_provider_token = payment_provider_token

        self.currency = ecommerce.get_currency()
        self.ecommerce = ecommerce

        self.product_detail_image_view_type = product_detail_image_view_type

        self.payment_need_name = payment_need_name
        self.payment_need_shipping_address = payment_need_shipping_address
        self.payment_need_phone_number = payment_need_phone_number
        self.payment_need_email = payment_need_email

        self.product_specification_separator = product_specification_separator

        application = Application.builder().token(token).build()

        self._add_handlers_to_tg_app(application)
        self.application = application
    
    def _add_handlers_to_tg_app(self, application):
        application.add_handler(CommandHandler('start', self._start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_user_text))
        application.add_handler(CallbackQueryHandler(self._show_orders, pattern=r'^orders$'))
        application.add_handler(CallbackQueryHandler(self._show_categories, pattern=r'^categories$'))
        application.add_handler(CallbackQueryHandler(self._show_products_in_category, pattern=r'^category:'))
        application.add_handler(CallbackQueryHandler(self._search_products_in_category, pattern=r'^search_in_category:'))
        application.add_handler(CallbackQueryHandler(self._handle_show_product, pattern=r'^product:'))
        application.add_handler(CallbackQueryHandler(self._pay_now, pattern=r'^pay_now$'))
        application.add_handler(PreCheckoutQueryHandler(self._pre_checkout_query))
        application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, self._successful_payment))
        application.add_handler(ConversationHandler(
            entry_points=[MessageHandler(filters.regex(r'^Get Recommendations$'), self._get_recommendations)],
            states={
                self.EcommerceTelegramBotState.RECOMMENDATIONS: [MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_request_for_product_recommendations)]
            },
            fallbacks=[],
        ))
        
        application.add_handler(CallbackQueryHandler(self._show_cart, pattern=r'^cart'))
        application.add_handler(CallbackQueryHandler(self._remove_one_item_from_cart, pattern=r'^remove_one_item_from_cart:'))
        application.add_handler(CallbackQueryHandler(self._add_one_item_to_cart, pattern=r'^add_one_item_to_cart:'))
        application.add_handler(CallbackQueryHandler(self._add_one_item_to_cart_and_notify_message, pattern=r'^add_one_item_to_cart_and_notify_message:'))
        application.add_handler(CallbackQueryHandler(self._remove_product_from_cart, pattern=r'^remove_product_from_cart:'))

    def _start(self, update: Update, context: CallbackContext) -> None:
        self._show_main_menu(update, context)

    async def _show_orders(self, update: Update, context: CallbackContext) -> None:
        # TODO
        pass

    async def _show_main_menu(self, update: Update, context: CallbackContext) -> None:
        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(_('Categories'), callback_data='categories')],
            [InlineKeyboardButton(_('Cart'), callback_data='cart')],
            [InlineKeyboardButton(_('Orders'), callback_data='orders')],
            [InlineKeyboardButton(_('Account'), callback_data='account')],
        ])

        if update.message:
            await update.message.reply_text(_('Main Menu:'), reply_markup=reply_markup)
        elif update.callback_query:
            query = update.callback_query
            await query.edit_message_text(_('Main Menu:'), reply_markup=reply_markup)

    async def _show_categories(self, update: Update, context: CallbackContext) -> None:
        categories = self.ecommerce.get_categories()

        keyboard = [
            [InlineKeyboardButton(category.name, callback_data=f"category:{category.id}")]
            for category in categories
        ]
        keyboard.append([InlineKeyboardButton(_('Back to Main Menu'), callback_data='main_menu')])
        reply_markup = InlineKeyboardMarkup(keyboard)

        query = update.callback_query
        await query.edit_message_text(_('Choose a category:'), reply_markup=reply_markup)

    async def _show_products_in_category(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()
        category_id = query.data.split(':')[1]
        category_id = int(category_id)

        product_list = self.ecommerce.browse_products(category_id=category_id)

        if product_list:
            keyboard = [
                [InlineKeyboardButton(product.name, callback_data=f"product:{product.id}")]
                for product in product_list
            ]
            keyboard.append([InlineKeyboardButton(_('Back to Categories'), callback_data='categories')])
            reply_markup = InlineKeyboardMarkup(keyboard)
            query.edit_message_text(_('Products in {category_id}:').format(category_id=category_id), reply_markup=reply_markup)
        else:
            query.edit_message_text(_('No products found in this category.'))

        await self._show_main_menu(update, context)

    async def _add_one_item_to_cart_and_notify_message(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(':')[1]
        product_id = int(product_id)

        user_id = query.from_user.id

        cart = self.ecommerce.get_cart(user_id)
        cart.add_item(product_id)

        query.message.reply_text(f"Product added to cart: #{product.id} {product.name}")

    async def get_cart_item_message_kwargs(self, item):
        product_id = item.product_id

        product = self.ecommerce.get_product_by_id(product_id)

        reply_markup = self.create_cart_item_inline_keyboard(product.id)
        keyboard = [
            [InlineKeyboardButton("+", callback_data=f"add_one_item_to_cart:{product_id}"),
             InlineKeyboardButton("-", callback_data=f"remove_one_item_from_cart:{product_id}"),
             InlineKeyboardButton("Remove", callback_data=f"remove_product_from_cart:{product_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        return {
            "text": "#{product_id} {product_name}: {quantity}".format(product_id=product.id, product_name=product.name, quantity=item.quantity),
            "reply_markup": reply_markup
        }

    async def edit_cart_item_message(self, query, item):
        await query.edit_message_text(**self.get_cart_item_message_kwargs(item))

    async def _add_one_item_to_cart(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(':')[1]
        product_id = int(product_id)

        user_id = query.from_user.id

        cart = self.ecommerce.get_cart(user_id)
        item = cart.add_item(product_id)

        await self.edit_cart_item_message(query, item)

    async def _remove_one_item_from_cart(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        cart = self.ecommerce.get_cart(user_id)
        item = cart.remove_item(product_id)

        if item is None:
            await query.delete_message()
        else:
            await self.edit_cart_item_message(query, item)

    async def _remove_product_from_cart(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        cart = self.ecommerce.get_cart(user_id)
        product_found = cart.remove_all_items_of_product(product_id)
        
        await query.delete_message()

        if product_found:
            await query.message.reply_text(_("Item removed from cart."))
        else:
            await query.message.reply_text(_("Item not found in cart."))

    async def _show_checkout(self, update: Update, context: CallbackContext) -> None:
        user_id = update.effective_user.id

        cart = self.ecommerce.get_cart(user_id)

        if len(cart) == 0:
            update.message.reply_text(_('Your cart is empty. Nothing to checkout.'))
            await self._show_main_menu(update, context)
            return

        cart_summary = cart.get_summary(summary_header=_("Checkout Summary:"))
        await update.message.reply_text(cart_summary)

        keyboard = [[InlineKeyboardButton(_('Pay Now'), callback_data='pay_now')]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(_("Proceed to payment?"), reply_markup=reply_markup)

        #keyboard = [[InlineKeyboardButton('Confirm Checkout', callback_data='confirm_checkout')]]
        #reply_markup = InlineKeyboardMarkup(keyboard)
        #update.message.reply_text("Confirm your order?", reply_markup=reply_markup)

    async def _show_cart(self, update: Update, context: CallbackContext) -> None:
        cart = self.ecommerce.get_cart(user_id)
        cart_items = cart.get_items()

        if len(cart_items) == 0:
            await update.message.reply_text(_('Your cart is empty.'))
            await self._show_main_menu(update, context)
            return

        for cart_item in cart_items:
            await query.reply_text(**self.get_cart_item_message_kwargs(cart_item))

        await self._show_main_menu(update, context)

    #def confirm_checkout(update: Update, context: CallbackContext) -> None:
    async def _pay_now(self, update: Update, context: CallbackContext) -> None:
        """
       * This function is called when the user clicks "Pay Now."
       * It creates an invoice using bot.send_invoice().
       * It sets the necessary parameters, including the provider_token, currency, prices, and other required details.
       * The price is now generated from the cart items.
       * Prices are now handled with the LabeledPrice object, and the total amount is calculated.
       * The price is now handled in cents, as required by telegram.
        """

        query = update.callback_query
        await query.answer()

        user_id = update.effective_user.id

        cart = self.ecommerce.get_cart(user_id)

        if len(cart) == 0:
            # query.message.reply_text('Cart is empty.')
            await query.message.reply_text(_('Your cart is empty.'))
            return

        total = Decimal()
        prices = []
        for item in cart:
            price = item.price
            item_total = item.quantity * Decimal(price)
            total += item_total
            prices.append(LabeledPrice(label=f"{item.name} x{item.quantity}", amount=item_total * 100)) #amount in cents
        context.user_data['prices'] = prices #store prices so we can use them in pre_checkout_query

        invoice_payload = self.get_invoice_payload(user_id, cart)

        # Another option: redirect to URL for payment
        # self._show_main_menu(update, context)

        await query.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=_('Your Order'),
            description=_('Payment for your order.'),
            payload=invoice_payload, #unique payload
            provider_token=self.payment_provider_token,
            currency=self.currency,
            prices=prices,
            start_parameter='start_parameter',
            need_shipping_address=self.payment_need_shipping_address,
            need_name=self.payment_need_name,
            need_phone_number=self.payment_need_phone_number,
            need_email=self.payment_need_email,
            is_flexible=False,
        )

    def get_invoice_payload(self, user_id, cart):
        return 'some-invoice-payload'

    def _pre_checkout_query(self, update: Update, context: CallbackContext) -> None:
        """
       * This function handles pre-checkout queries, which are sent by Telegram to verify the payment details.
       * It checks if the total amount matches the expected amount.
       * It answers the query with ok=True or ok=False based on the verification result.
        """

        query = update.pre_checkout_query
        if query.total_amount != sum(price.amount for price in context.user_data['prices']):
            query.answer(ok=False, error_message="Price mismatch")
        else:
            query.answer(ok=True)

    async def _successful_payment(self, update: Update, context: CallbackContext) -> None:
        """
       * This function is called when the payment is successful.
       * It sends a confirmation message and clears the user's cart.
        """

        await update.message.reply_text("Payment successful! Thank you for your purchase.")
        user_id = update.effective_user.id

        cart = self.ecommerce.get_cart(user_id)
        cart.clear()

        await self._show_main_menu(update, context)

    async def _get_recommendations(self, update: Update, context: CallbackContext) -> None:
        await update.message.reply_text(_('Please tell me what you are looking for, and I will recommend some products:'))
        return self.EcommerceTelegramBotState.RECOMMENDATIONS # Wait for user's description

    def _get_relevant_products(self, user_recommendation_request):
        # TODO: RAG system indexing the title and description of the product
        products = self.ecommerce.get_all_products()
        return products

    def _create_specifications_of_relevant_products(self, user_recommendation_request):
        # NOTE: Another option is to use tabulate python library and print the products in a table with github format
        products = self._get_relevant_products(user_recommendation_request)

        product_specifications = []
        for product in products:
            product_specification = f"""
Product ID: {product.id}
Product name: {product.name}
Price: {product.price}
Description: {product.description}"""
            product_specifications.append(product_specification)

        return self.product_specification_separator.join(product_specifications)

    async def _generate_recommendations(self, user_recommendation_request):
        product_specifications = self._create_specifications_of_relevant_products(user_recommendation_request)

        try:
            response = await litellm.acompletion(
                model=self.llm_model,
                messages=[
                    {"role": "user", "content": f"""These are the available relevant products: 
{product_specifications}

---
Recommend a list of products to the user. Return a list of products with its product ID and product name in JSON format. Example of JSON output:
{
    "products": [
        {
            "id": 2323
            "name": "product name of 2323",
        },
        {
            "id": 973
            "name": "product name of 973",
        }
    ]
}.
Recommend products based on the user's request:
{user_recommendation_request}"""},
                ],
                response_format={"type": "json_object"},
                temperature=self.llm_temperature,
            )
            recommendations = json.loads(response.choices[0].message.content.strip())
            return recommendations["products"]
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
            return None

    async def _handle_request_for_product_recommendations(self, update: Update, context: CallbackContext) -> None:
        # TODO: Navigation to product detail
        user_recommendation_request = update.message.text
        recommendations = await self._generate_recommendations(user_recommendation_request)
        if recommendations:
            recommendation_text = _("Here there are some recommendations:\n")
            for product in recommendations:
                recommendation_text += f"- {product['id']}: {product['name']}\n"
            await update.message.reply_text(recommendation_text)
        else:
            await update.message.reply_text(_("Sorry, I couldn't find any recommendations."))

        return ConversationHandler.END

    async def _handle_user_text(self, update: Update, context: CallbackContext) -> None:
        text = update.message.text
        # TODO: Search product using title and description fields

    def create_product_carousel_inline_markup(self, product, product_image_index=0):
        if product_image_index == 0:
            carousel_buttons_navigation = [
                InlineKeyboardButton(_('Next'), callback_data=f"change_product_carousel_image:{product.id}:{product_image_index + 1}"),
            ]
        elif product_image_index == len(product.image_urls) - 1:
            carousel_buttons_navigation = [
                InlineKeyboardButton(_('Previous'), callback_data=f"change_product_carousel_image:{product.id}:{product_image_index - 1}")
            ]
        else:
            carousel_buttons_navigation = [
                InlineKeyboardButton(_('Previous'), callback_data=f"change_product_carousel_image:{product.id}:{product_image_index - 1}"),
                InlineKeyboardButton(_('Next'), callback_data=f"change_product_carousel_image:{product.id}:{product_image_index + 1}"),
            ]

        return InlineKeyboardMarkup([carousel_buttons_navigation])

    async def _handle_change_product_carousel_image(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id, product_image_index = query.data.split(':')[1:]
        product_id = int(product_id)
        product_image_index = int(product_image_index)
        product_image_index = max(0, min(product_image_index, len(product.image_urls) - 1)) #Ensure index is in range.

        product = self.ecommerce.get_product_by_id(product_id)
        if product is None:
            await query.message.reply_text(f"Product does not exist: #{product_id}")
            return

        reply_markup = self.create_product_carousel_inline_markup(product, product_image_index=product_image_index)

        media = InputMediaPhoto(media=product.image_urls[product_image_index])
        await query.edit_message_media(media=media, reply_markup=reply_markup)

    async def _handle_show_product(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(':')[1]
        product_id = int(product_id)

        product = self.ecommerce.get_product_by_id(product_id)
        if product is None:
            await query.message.reply_text(f"Product does not exist: #{product_id}")
            return

        # TODO: Show breadcumb navigation
        product_text = f"*{product.name}*\n\n{product.description}\n\nPrice: {product.price}"

        if product.image_urls is not None and len(product.image_urls) != 0:
            if len(product.image_urls) == 1:
                await query.message.reply_photo(photo=InputMediaPhoto(media=product.image_urls[0]))
            else:
                if self.product_detail_image_view_type == ProductDetailImageViewType.IMAGE_GALLERY:
                    # Image gallery                    
                    media = [InputMediaPhoto(media=url) for url in product.image_urls]
                    await query.message.reply_media_group(media=media)
                else:
                    # Carousel
                    reply_markup = self.create_product_carousel_inline_markup(product, 0)
                    await query.message.reply_photo(photo=InputMediaPhoto(media=product.image_urls[0]), reply_markup=reply_markup)

        reply_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton(_('Add to cart'), callback_data=f"add_one_item_to_cart_and_notify_message:{product_id}")],
            [InlineKeyboardButton(_('Cart'), callback_data="cart"),],
            [InlineKeyboardButton(_("Back to Categories"), callback_data="categories")]
        ])

        await query.message.reply_text(product_text, reply_markup=reply_markup, parse_mode=telegram.ParseMode.MARKDOWN)

    def run(self):
        self.application.run(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    ecommerce = EcommerceDemo()

    bot = EcommerceTelegramBot(token=BOT_TOKEN, ecommerce=ecommerce, payment_provider_token=PAYMENT_PROVIDER_TOKEN)
    bot.run()


if __name__ == '__main__':
    main()