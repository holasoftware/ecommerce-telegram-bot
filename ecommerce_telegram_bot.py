import os
import logging
import random
import json
from decimal import Decimal
from dataclasses import dataclass, field
from enum import Enum, auto
import datetime

# litellm is an optional dependency
try:
    import litellm
except ImportError:
    litellm = None

import telegram
from telegram import (
    Update,
    ReplyKeyboardMarkup,
    KeyboardButton,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    LabeledPrice,
    InputMediaPhoto,
    ForceReply,
    BotCommand,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    CallbackContext,
    PreCheckoutQueryHandler,
    ShippingQueryHandler,
    ConversationHandler,
    ApplicationBuilder,
)
from telegram.constants import ParseMode
from telegram.helpers import escape_markdown


logger = logging.getLogger(__name__)


BOT_TOKEN = os.getenv("BOT_TELEGRAM_TOKEN")

# PAYMENT_PROVIDER_TOKEN: You need to replace this with your actual payment provider token (e.g., Stripe, Yandex.Checkout)
PAYMENT_PROVIDER_TOKEN = os.environ.get("BOT_TELEGRAM_PAYMENT_PROVIDER_TOKEN")
LANGUAGE_CODE = os.getenv("BOT_TELEGRAM_LANGUAGE_CODE", "en")

translations = {"en": {}}


def _(text):
    """Translate text according to the current language"""
    return translations.get(LANGUAGE_CODE, {}).get(text, text)


def md_bold(text):
    return "*" + text + "*"


def md_monospace_font(text):
    return "`" + text + "`"


@dataclass
class Money:
    currency: str
    currency_symbol: str
    localized_price_template: str

    def format_price(self, price):
        return self.localized_price_template.format(
            currency_symbol=self.currency_symbol, price=price
        )


money_locale = {
    "us": Money(
        currency="USD",
        currency_symbol="$",
        localized_price_template="{currency_symbol}{price:.2f}",
    )
}


@dataclass
class OrderLine:
    order_id: int
    product_id: int
    product_name: str
    unit_price: Decimal
    quantity: int
    discount: float | None = None
    variant_id: int | None = None

    def total_line_price(self) -> Decimal:
        price = self.quantity * self.unit_price * (1 - discount)

        if self.discount is not None:
            price = price * (1 - self.discount)

        return price


@dataclass
class Order:
    id: int
    telegram_user_id: int
    customer_id: int
    payed: bool
    delivered: bool
    order_lines: list[OrderLine] = field(default_factory=list)
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now()
    )

    def total_order_price(self) -> Decimal:
        return sum([line.total_line_price() for line in self.order_lines])


@dataclass
class OrderHistoryPage:
    telegram_user_id: int
    customer_id: int
    page: int
    num_orders_per_page: int
    orders: list[Order] = field(default_factory=list)


@dataclass
class CartItem:
    product_id: int
    name: str
    unit_price: Decimal
    quantity: int
    discount: float | None = None
    variant_id: int | None = None


@dataclass
class CartSession:
    telegram_user_id: int
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
    parent_id: int | None = None
    product_ids: list[int] | None = None
    subcategory_ids: list[int] | None = None


@dataclass
class Product:
    id: int
    name: str
    category_id: int
    price: Decimal
    stock: int
    description: str | None = None
    discount: float | None = None
    variants: list[ProductVariant] | None = None
    image_urls: list[str] | None = None
    is_digital_product: bool = False

    @property
    def has_variants(self):
        return self.variants != None and len(self.variants) != 0

    @property
    def has_stock(self):
        if self.has_variants():
            if variant in self.variants:
                if variant.stock > 0:
                    return True
            return False
        else:
            return self.stock > 0


class ShoppingCart:
    """
    A class to represent a shopping cart for an e-commerce bot.
    Data is stored in memory (replace with a database in a real application).
    """

    def __init__(self, ecommerce, telegram_user_id):
        self.ecommerce = ecommerce
        self.telegram_user_id = telegram_user_id

    async def get_items(self):
        raise NotImplementedError

    async def add_product(self, product_id, quantity=1):
        """Adds a product to the user'sawait cart."""
        raise NotImplementedError

    async def remove_product(self, product_id, quantity=1):
        """Removes a product from the user's cart by its product ID."""
        raise NotImplementedError

    async def remove_item(self, product_id):
        """Removes a product from the user's cart by its product ID."""
        raise NotImplementedError

    async def clear(self):
        raise NotImplementedError

    async def get_num_items(self):
        raise NotImplementedError

    async def has_product_by_id(self, product_id):
        raise NotImplementedError

    async def get_session_data(self):
        items = await self.get_items()
        return CartSession(telegram_user_id=self.telegram_user_id, items=items)

    async def is_empty(self):
        return await self.get_num_items() == 0

    async def has_items(self):
        return not await self.is_empty()

    async def get_num_products(self):
        return sum([item.quantity for item in await self.get_items()])

    def calculate_item_total(self, item):
        item_total = item.quantity * Decimal(item.unit_price)

        if item.discount:
            item_total = item_total * (1 - item.discount)

        return item_total

    async def calculate_total(self):
        """Calculates the total price of the items in the user'sawait cart."""
        cart_items = await self.get_items()
        total = Decimal()
        for item in cart_items:
            item_total = self.calculate_item_total(item)
            total += item_total
        return total

    async def get_summary(
        self,
        summary_header=_("Your Cart:"),
        cart_empty_text=_("Your cart is empty."),
        total_text=_("Total"),
    ):
        """Returns a string summary of the user'sawait cart."""
        cart_items = await self.get_items()
        if not cart_items:
            return cart_empty_text

        money = await self.ecommerce.get_money_locale(self.telegram_user_id)

        summary = summary_header + "\n"

        total_price = Decimal()
        for item in cart_items:
            item_total = self.calculate_item_total(item)

            localized_item_unit_price = money.format_price(item.unit_price)
            localized_item_total = money.format_price(item_total)

            total_price += item_total

            if item.discount:
                summary_row = f"- {item.name} ({localized_item_unit_price}) x{item.quantity} = {localized_item_total}({item.discount * 100:.0f}% off)\n"
            else:
                summary_row = f"- {item.name} ({localized_item_unit_price}) x{item.quantity} = {localized_item_total}\n"

            summary += summary_row

        localized_cart_total_price = money.format_price(total_price)

        summary += "\n" + total_text + ": " + localized_cart_total_price
        return summary

    async def __aiter__(self):
        items = await self.get_items()
        return iter(items)

    async def has(self, product):
        if isinstance(product, Product):
            product_id = product.id
        elif isinstance(product, int):
            product_id = product
        else:
            raise ValueError(
                "Not possible to use this object in the 'in' operator: %r" % product
            )

        return await self.has_product_by_id(product_id)


class Ecommerce:
    """
    A class to represent the backend of an e-commerce engine.
    """

    shopping_cart_class = ShoppingCart
    default_locale = "us"

    async def get_money_locale(self, telegram_user_id=None):
        return money_locale[self.default_locale]

    async def browse_products(self, q=None, category_id=None, limit=None):
        raise NotImplementedError

    async def get_order_history(self, telegram_user_id):
        raise NotImplementedError

    async def get_all_products(self):
        return await self.browse_products()

    async def get_product(self, product_id):
        raise NotImplementedError

    async def get_category(self, category_id):
        raise NotImplementedError

    async def get_categories(self, parent_id=None):
        raise NotImplementedError

    def get_cart(self, telegram_user_id):
        return self.shopping_cart_class(self, telegram_user_id)

    async def get_currency(self):
        return await self.get_money_locale().currency


class ShoppingCartDemo(ShoppingCart):
    user_items = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._items = self.user_items.setdefault(self.telegram_user_id, [])

    def _find_item_by_product_id(self, product_id, variant_id=None):
        for i, item in enumerate(self._items):
            if item.product_id == product_id and item.variant_id == variant_id:
                return True, i, item

        return False, None, None

    async def add_product(self, product_id, quantity=1, variant_id=None):
        found, item_index, item = self._find_item_by_product_id(
            product_id, variant_id=variant_id
        )

        if found:
            item.quantity += quantity
        else:
            product = await self.ecommerce.get_product(product_id)

            item = CartItem(
                product_id=product_id,
                name=product.name,
                quantity=quantity,
                unit_price=product.price,
                discount=product.discount,
                variant_id=variant_id,
            )
            self._items.append(item)

        return item

    async def remove_product(self, product_id, quantity=1, variant_id=None):
        found, item_index, item = self._find_item_by_product_id(
            product_id, variant_id=variant_id
        )

        if found:
            if item.quantity <= quantity:
                del self._items[item_index]
            else:
                item.quantity -= quantity

                if item.quantity != 0:
                    return item
        else:
            return False

    async def remove_item(self, product_id, variant_id=None):
        found, item_index, item = self._find_item_by_product_id(
            product_id, variant_id=variant_id
        )

        if found:
            del self._items[item_index]
            return True
        else:
            return False

    async def get_items(self):
        return self._items

    async def clear(self):
        self._items = []

    async def get_num_items(self):
        return len(self._items)

    async def has_product_by_id(self, product_id):
        found, _, _ = self._find_item_by_product_id(product_id)
        return found


class EcommerceDemo(Ecommerce):
    """
    Ecommerce for demo purposes. Data is stored in memory
    """

    shopping_cart_class = ShoppingCartDemo

    categories = [
        ProductCategory(name="Electronics", id=0, subcategory_ids=[4, 5]),
        ProductCategory(name="Clothing", id=1, subcategory_ids=[6, 7, 8]),
        ProductCategory(name="Books", id=2, subcategory_ids=[9, 10]),
        ProductCategory(name="Home & Kitchen", id=3),
        ProductCategory(name="Laptops", id=4, parent_id=0),
        ProductCategory(name="Smartphones", id=5, parent_id=0),
        ProductCategory(name="T-shirts", id=6, parent_id=1),
        ProductCategory(name="Jeans", id=7, parent_id=1),
        ProductCategory(name="Caps", id=8, parent_id=1),
        ProductCategory(name="Fiction", id=9, parent_id=2),
        ProductCategory(name="Non-fiction", id=10, parent_id=2),
    ]

    def __init__(self):
        self._carts = {}
        self._products = []
        self._products_in_category = []

        self._generate_demo_data()

    async def get_categories(self, parent_id=None):
        if parent_id is None:
            return self.categories
        else:
            return [
                category
                for category in self.categories
                if category.parent_id == parent_id
            ]

    async def browse_products(self, q=None, category_id=None, limit=None):
        if category_id is None:
            found_products = self._products
        else:
            product_ids = self._products_in_category[category_id]
            found_products = []
            for product_id in product_ids:
                found_products.append(self._products[product_id])

        if len(found_products) == 0:
            return found_products

        if q is not None:
            q = q.lower()
            found_products = [
                product
                for product in found_products
                if q in product.name.lower()
                or (
                    product.description is not None and q in product.description.lower()
                )
            ]

        if limit is not None:
            found_products = found_products[:limit]

        return found_products

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

                products.append(
                    Product(
                        id=product_id,
                        category_id=category_id,
                        name="Product %d" % product_id,
                        image_urls=[
                            "https://fakeimg.pl/150",
                            "https://fakeimg.pl/200",
                            "https://fakeimg.pl/250",
                        ],
                        stock=self._get_random_product_stock(),
                        description="This is product %d." % product_id,
                        price=Decimal(random.randint(1, 1000)),
                    )
                )

    def _get_random_num_products_in_category(self):
        return random.randint(1, 5)

    def _get_random_product_stock(self):
        return random.randint(1, 10)

    async def get_product(self, product_id):
        return self._products[product_id]

    async def get_category(self, category_id):
        return self.categories[category_id]


# TODO: 2 modes 'command' mode and 'search' mode. The default mode is 'search' mode
class EcommerceTelegramBot:
    class EcommerceTelegramBotState(Enum):
        RECOMMENDATIONS = auto()
        SEARCH_PRODUCTS = auto()
        SEARCH_PRODUCTS_IN_CATEGORY = auto()

    class ProductDetailImageViewType(Enum):
        IMAGE_GALLERY = auto()
        CAROUSEL = auto()
        MAIN_PHOTO = auto()

    def __init__(
        self,
        token,
        ecommerce,
        product_recommendations_enabled=False,
        payment_provider_token=None,
        llm_model="gpt-3.5-turbo",
        llm_temperature=0.7,
        language="en",
        main_menu_title=_("Main Menu"),
        payment_need_name=True,
        payment_need_shipping_address=True,
        payment_need_phone_number=True,
        payment_need_email=True,
        product_specification_separator="\n\n--------------------\n\n",
        product_detail_image_view_type=ProductDetailImageViewType.CAROUSEL,
        welcome_message=_("Welcome to ecommerce bot!"),
    ):

        if not token:
            raise Exception("Telegram token is required")

        self.token = token
        self.llm_model = llm_model
        self.llm_temperature = llm_temperature

        if product_recommendations_enabled and litellm is None:
            logger.warn(
                "Module 'litellm' is not installed. Not possible to use product recommendations feature"
            )
            product_recommendations_enabled = False

        self.product_recommendations_enabled = product_recommendations_enabled
        self.payment_provider_token = payment_provider_token

        self.currency = ecommerce.get_currency()
        self.ecommerce = ecommerce
        self.welcome_message = welcome_message

        self.main_menu_title = main_menu_title

        self.product_detail_image_view_type = product_detail_image_view_type

        self.payment_need_name = payment_need_name
        self.payment_need_shipping_address = payment_need_shipping_address
        self.payment_need_phone_number = payment_need_phone_number
        self.payment_need_email = payment_need_email

        self.product_specification_separator = product_specification_separator

        application = (
            Application.builder().token(token).post_init(self._post_init).build()
        )

        self._add_handlers_to_tg_app(application)
        self.application = application

    def _add_handlers_to_tg_app(self, application):
        application.add_handler(CommandHandler("start", self._start))
        application.add_handler(CommandHandler("main_menu", self._show_main_menu))
        application.add_handler(CommandHandler("account", self._show_account))
        application.add_handler(CommandHandler("categories", self._show_categories))
        application.add_handler(CommandHandler("orders", self._show_orders))
        application.add_handler(CommandHandler("cart", self._show_cart))
        application.add_handler(
            CommandHandler("search", self._start_search_command_handler)
        )
        application.add_handler(
            CallbackQueryHandler(self._show_orders, pattern=r"^orders$")
        )
        application.add_handler(
            CallbackQueryHandler(
                self._show_categories_in_new_message,
                pattern=r"^show_categories_in_new_message$",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._show_main_menu, pattern=r"^main_menu$")
        )
        application.add_handler(
            CallbackQueryHandler(
                self._show_main_menu, pattern=r"^main_menu_in_new_message$"
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._show_categories, pattern=r"^categories$")
        )
        application.add_handler(
            CallbackQueryHandler(self._show_content_in_category, pattern=r"^category:")
        )

        application.add_handler(
            CallbackQueryHandler(self._show_product, pattern=r"^product:")
        )
        application.add_handler(
            CallbackQueryHandler(
                self._handle_change_product_carousel_image,
                pattern=r"^change_product_carousel_image:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._pay_now, pattern=r"^pay_now$")
        )
        application.add_handler(
            CallbackQueryHandler(self._show_checkout, pattern=r"^checkout$")
        )
        application.add_handler(PreCheckoutQueryHandler(self._pre_checkout_query))
        application.add_handler(
            MessageHandler(filters.SUCCESSFUL_PAYMENT, self._successful_payment)
        )

        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CallbackQueryHandler(
                        self._start_search_callback_query_handler,
                        pattern=r"^start_search$",
                    )
                ],
                states={
                    self.EcommerceTelegramBotState.SEARCH_PRODUCTS: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self._handle_search_products,
                        )
                    ]
                },
                fallbacks=[],
                per_message=True,
            )
        )

        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CallbackQueryHandler(
                        self._start_search_in_category,
                        pattern=r"^start_search_in_category:",
                    )
                ],
                states={
                    self.EcommerceTelegramBotState.SEARCH_PRODUCTS_IN_CATEGORY: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self._handle_search_products_in_category,
                        )
                    ]
                },
                fallbacks=[],
                per_message=True,
            )
        )

        application.add_handler(
            ConversationHandler(
                entry_points=[
                    CommandHandler("recommendations", self._get_recommendations)
                ],
                states={
                    self.EcommerceTelegramBotState.RECOMMENDATIONS: [
                        MessageHandler(
                            filters.TEXT & ~filters.COMMAND,
                            self._handle_request_for_product_recommendations,
                        )
                    ]
                },
                fallbacks=[],
            )
        )

        application.add_handler(CallbackQueryHandler(self._show_cart, pattern=r"^cart"))
        application.add_handler(
            CallbackQueryHandler(
                self._remove_one_product_from_cart,
                pattern=r"^remove_one_product_from_cart:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._add_one_product_to_cart, pattern=r"^add_one_product_to_cart:"
            )
        )
        application.add_handler(
            CallbackQueryHandler(
                self._add_to_cart,
                pattern=r"^add_to_cart:",
            )
        )
        application.add_handler(
            CallbackQueryHandler(self._remove_cart_item, pattern=r"^remove_cart_item:")
        )

        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_user_text)
        )

    async def _start_search_command_handler(
        self, update: Update, context: CallbackContext
    ) -> None:
        await update.message.reply_text(
            _("Write here your query:"), reply_markup=ForceReply(selective=True)
        )

        return self.EcommerceTelegramBotState.SEARCH_PRODUCTS

    async def _start_search_callback_query_handler(
        self, update: Update, context: CallbackContext
    ) -> None:
        query = update.callback_query
        await query.answer()

        await query.message.reply_text(
            _("Write here your query:"), reply_markup=ForceReply(selective=True)
        )

        return self.EcommerceTelegramBotState.SEARCH_PRODUCTS

    async def _handle_search_products(
        self, update: Update, context: CallbackContext, category_id=None
    ) -> None:
        q = update.message.text

        product_list = await self.ecommerce.browse_products(
            q=q, category_id=category_id
        )

        if len(product_list) == 0:
            await update.message.reply_text(_("No product found"))
        else:
            keyboard = [
                [
                    InlineKeyboardButton(
                        product.name, callback_data=f"product:{product.id}"
                    )
                ]
                for product in product_list
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                _("Search results:"), reply_markup=reply_markup
            )

        return ConversationHandler.END

    async def _start(self, update: Update, context: CallbackContext) -> None:
        if self.welcome_message is not None:
            await update.message.reply_text(self.welcome_message)

        await self._show_main_menu(update, context, edit_previous_message=False)

    async def _show_orders(self, update: Update, context: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            await query.answer()

    async def _show_account(self, update: Update, context: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            await query.answer()

    def get_main_menu_reply_markup(self):
        keyboard = [
            [InlineKeyboardButton(_("Categories"), callback_data="categories")],
            [InlineKeyboardButton(_("Cart"), callback_data="cart")],
            [InlineKeyboardButton(_("Orders"), callback_data="orders")],
            [InlineKeyboardButton(_("Account"), callback_data="account")],
        ]
        if self.product_recommendations_enabled:
            keyboard.append(
                [
                    InlineKeyboardButton(
                        _("Product recommendations"), callback_data="recommendations"
                    )
                ]
            )

        reply_markup = InlineKeyboardMarkup(keyboard)

        return reply_markup

    async def _show_main_menu(
        self, update: Update, context: CallbackContext, edit_previous_message=True
    ) -> None:
        reply_markup = self.get_main_menu_reply_markup()

        if update.message:
            await update.message.reply_text(
                self.main_menu_title, reply_markup=reply_markup
            )
        elif update.callback_query:
            query = update.callback_query
            await query.answer()

            if edit_previous_message:
                await query.edit_message_text(
                    self.main_menu_title, reply_markup=reply_markup
                )
            else:
                await query._get_message().reply_text(
                    self.main_menu_title, reply_markup=reply_markup
                )

    async def _show_main_menu_in_new_message(
        self, update: Update, context: CallbackContext
    ):
        await self._show_main_menu(update, context, edit_previous_message=False)

    async def _show_categories(
        self, update: Update, context: CallbackContext, edit_previous_message=True
    ) -> None:
        if update.callback_query:
            query = update.callback_query
            await query.answer()

        categories = await self.ecommerce.get_categories()

        # TODO: Show num products in each category
        keyboard = [
            [
                InlineKeyboardButton(
                    category.name, callback_data=f"category:{category.id}:0"
                )
            ]
            for category in categories
        ]
        keyboard.append(
            [InlineKeyboardButton(_("Back to Main Menu"), callback_data="main_menu")]
        )
        reply_markup = InlineKeyboardMarkup(keyboard)

        choose_category_text = _("Choose a category:")

        if update.message:
            await update.message.reply_text(
                choose_category_text, reply_markup=reply_markup
            )
        elif update.callback_query:
            if edit_previous_message:
                await query.edit_message_text(
                    choose_category_text, reply_markup=reply_markup
                )
            else:
                await query._get_message().reply_text(
                    choose_category_text, reply_markup=reply_markup
                )

    async def _show_categories_in_new_message(
        self, update: Update, context: CallbackContext
    ):
        await self._show_categories(update, context, edit_previous_message=False)

    async def _show_content_in_category(
        self, update: Update, context: CallbackContext
    ) -> None:
        # TODO: Show pagination if the number of products in the category is big

        query = update.callback_query
        await query.answer()

        telegram_user_id = query.from_user.id

        category_id, use_new_message = query.data.split(":")[1:]
        category_id = int(category_id)
        use_new_message = use_new_message == "1"

        category = await self.ecommerce.get_category(category_id)

        cart = self.ecommerce.get_cart(telegram_user_id)
        num_products_in_cart = await cart.get_num_products()

        product_list = await self.ecommerce.browse_products(category_id=category_id)

        if product_list:
            keyboard = [
                [
                    InlineKeyboardButton(
                        product.name, callback_data=f"product:{product.id}"
                    )
                ]
                for product in product_list
            ]
            keyboard.append(
                [
                    InlineKeyboardButton(
                        (
                            _("Cart")
                            + " ({num_products_in_cart})".format(
                                num_products_in_cart=num_products_in_cart
                            )
                            if num_products_in_cart > 0
                            else ""
                        ),
                        callback_data="cart",
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        _("Back to categories"),
                        callback_data="show_categories_in_new_message",
                    )
                ]
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        _("Search products in this category"),
                        callback_data=f"start_search_in_category:{category_id}",
                    )
                ]
            )
            reply_markup = InlineKeyboardMarkup(keyboard)
            text = _("Products in category {category_name}:").format(
                category_name=category.name
            )

            if use_new_message:
                await query._get_message().reply_text(
                    text,
                    reply_markup=reply_markup,
                )
            else:
                await query.edit_message_text(
                    text,
                    reply_markup=reply_markup,
                )
        else:
            text = _("No products found in this category.")
            if use_new_message:
                await query._get_message().reply_text(text)
            else:
                await query.edit_message_text(text)

    async def _start_search_in_category(
        self, update: Update, context: CallbackContext
    ) -> None:
        query = update.callback_query
        await query.answer()

        category_id = query.data.split(":")[1]
        category_id = int(category_id)

        context.user_data["category_id"] = category_id
        await query._get_message().reply_text(
            _("Write here your query:"), reply_markup=ForceReply(selective=True)
        )

        return self.EcommerceTelegramBotState.SEARCH_PRODUCTS_IN_CATEGORY

    async def _handle_search_products_in_category(
        self, update: Update, context: CallbackContext
    ) -> None:
        category_id = context.user_data["category_id"]
        return await self._handle_search_products(
            update=update, context=context, category_id=category_id
        )

    async def _add_to_cart(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        product = await self.ecommerce.get_product(product_id)

        if product.has_variants:
            await self._show_product_variants(product, update, context)
            return

        telegram_user_id = query.from_user.id

        cart = self.ecommerce.get_cart(telegram_user_id)
        cart_item = await cart.add_product(product_id)

        num_products_in_cart = await cart.get_num_products()

        keyboard = [
            [
                InlineKeyboardButton(
                    _("Cart ({num_products_in_cart})").format(
                        num_products_in_cart=num_products_in_cart
                    ),
                    callback_data="cart",
                )
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # TODO: Maybe to update previous message
        await query.message.reply_text(
            _(
                "Product added to cart: {product_name} ({item_quantity})\nTotal items: {num_products_in_cart}"
            ).format(
                item_quantity=cart_item.quantity,
                product_name=product.name,
                num_products_in_cart=num_products_in_cart,
            ),
            reply_markup=reply_markup,
        )

    def _show_product_variants(
        self, product: Product, update: Update, context: CallbackContext
    ) -> None:
        pass

    def create_cart_item_inline_keyboard(self, product_id):
        keyboard = [
            [
                InlineKeyboardButton(
                    "-", callback_data=f"remove_one_product_from_cart:{product_id}"
                ),
                InlineKeyboardButton(
                    "+", callback_data=f"add_one_product_to_cart:{product_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    _("Remove"), callback_data=f"remove_cart_item:{product_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    _("See product"), callback_data=f"product:{product_id}"
                ),
            ],
        ]

        return keyboard

    async def get_cart_item_message_kwargs(self, item):
        product_id = item.product_id

        product = await self.ecommerce.get_product(product_id)

        keyboard = self.create_cart_item_inline_keyboard(product.id)
        reply_markup = InlineKeyboardMarkup(keyboard)

        return {
            "text": "{product_name} ({quantity})\n".format(
                product_name=product.name, quantity=item.quantity
            ),
            "reply_markup": reply_markup,
        }

    async def edit_cart_item_message(self, query, cart_item):
        await query.edit_message_text(
            **await self.get_cart_item_message_kwargs(cart_item)
        )

    async def _add_one_product_to_cart(
        self, update: Update, context: CallbackContext
    ) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        telegram_user_id = query.from_user.id

        cart = self.ecommerce.get_cart(telegram_user_id)
        cart_item = await cart.add_product(product_id)

        num_products_in_cart = await cart.get_num_products()

        await query.message.reply_text(
            _(
                "One product added to cart: {item_name} ({item_quantity})\nTotal items: {num_products_in_cart}"
            ).format(
                item_quantity=cart_item.quantity,
                item_name=cart_item.name,
                num_products_in_cart=num_products_in_cart,
            )
        )

        await self.edit_cart_item_message(query, cart_item)

    async def _remove_one_product_from_cart(
        self, update: Update, context: CallbackContext
    ) -> None:
        query = update.callback_query
        await query.answer()

        telegram_user_id = query.from_user.id
        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        cart = self.ecommerce.get_cart(telegram_user_id)

        cart_item = await cart.remove_product(product_id)
        num_products_in_cart = await cart.get_num_products()

        if cart_item is None:
            await query.delete_message()

            if num_products_in_cart == 0:
                await query.message.reply_text(_("Item removed.\nCart is empty."))
            else:
                await query.message.reply_text(
                    _("Item removed.\nTotal items: {num_products_in_cart}").format(
                        num_products_in_cart=num_products_in_cart
                    )
                )
        else:
            await query.message.reply_text(
                _(
                    "One product removed from cart: {item_name} ({item_quantity})\nTotal items: {num_products_in_cart}"
                ).format(
                    item_quantity=cart_item.quantity,
                    item_name=cart_item.name,
                    num_products_in_cart=num_products_in_cart,
                )
            )

            await self.edit_cart_item_message(query, cart_item)

    async def _remove_cart_item(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        telegram_user_id = query.from_user.id
        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        cart = self.ecommerce.get_cart(telegram_user_id)
        item_found = await cart.remove_item(product_id)

        await query.delete_message()

        if item_found:
            num_products_in_cart = await cart.get_num_products()

            if num_products_in_cart == 0:
                await query._get_message().reply_text(
                    _("Item removed fromawait cart.\nCart is empty.")
                )
            else:
                await query._get_message().reply_text(
                    _(
                        "Item removed fromawait cart.\nTotal items: {num_products_in_cart}"
                    ).format(num_products_in_cart=num_products_in_cart)
                )
        else:
            await query._get_message().reply_text(_("Item not found inawait cart."))

    async def _show_checkout(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        telegram_user_id = query.from_user.id
        message = query._get_message()

        cart = self.ecommerce.get_cart(telegram_user_id)

        if await cart.get_num_products() == 0:
            keyboard = [
                [
                    InlineKeyboardButton(
                        _("Show main menu"), callback_data="main_menu_in_new_message"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(
                _("Nothing to checkout. Your cart is empty."), reply_markup=reply_markup
            )
            return

        checkout_header_text = _("Checkout Summary")
        checkout_header = (
            ("-" * len(checkout_header_text))
            + "\n"
            + checkout_header_text
            + "\n"
            + ("-" * len(checkout_header_text))
            + "\n"
        )
        cart_summary = await cart.get_summary(summary_header=checkout_header)
        cart_summary = md_monospace_font(escape_markdown(cart_summary))

        await message.reply_text(cart_summary, parse_mode=ParseMode.MARKDOWN)

        keyboard = [
            [InlineKeyboardButton(_("Pay Now"), callback_data="pay_now")],
            [InlineKeyboardButton(_("Back to Cart"), callback_data="cart")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(_("Proceed to payment?"), reply_markup=reply_markup)

        # keyboard = [[InlineKeyboardButton('Confirm Checkout', callback_data='confirm_checkout')]]
        # reply_markup = InlineKeyboardMarkup(keyboard)
        # update.message.reply_text("Confirm your order?", reply_markup=reply_markup)

    async def _show_cart(self, update: Update, context: CallbackContext) -> None:
        if update.callback_query:
            query = update.callback_query
            await query.answer()

            telegram_user_id = query.from_user.id
            message = query._get_message()
        else:
            telegram_user_id = update.effective_user.id
            message = update.message

        cart = self.ecommerce.get_cart(telegram_user_id)
        cart_items = await cart.get_items()

        if len(cart_items) == 0:
            keyboard = [
                [
                    InlineKeyboardButton(
                        _("Show main menu"), callback_data="main_menu_in_new_message"
                    )
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await message.reply_text(
                _("Your cart is empty."), reply_markup=reply_markup
            )
            return

        await message.reply_text(
            md_bold(_("Cart").upper()), parse_mode=ParseMode.MARKDOWN
        )

        for cart_item in cart_items:
            await message.reply_text(
                **await self.get_cart_item_message_kwargs(cart_item)
            )

        keyboard = [[InlineKeyboardButton(_("Check Out"), callback_data="checkout")]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await message.reply_text(_("Proceed to checkout?"), reply_markup=reply_markup)

    # def confirm_checkout(update: Update, context: CallbackContext) -> None:
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

        telegram_user_id = query.from_user.id

        cart = self.ecommerce.get_cart(telegram_user_id)

        if await cart.get_num_products() == 0:
            # query.message.reply_text('Cart is empty.')
            await query._get_message().reply_text(_("Your cart is empty."))
            return

        total = Decimal()
        prices = []
        for item in cart:
            price = item.price
            item_total = item.quantity * Decimal(price)
            total += item_total
            prices.append(
                LabeledPrice(
                    label=f"{item.name} x{item.quantity}", amount=item_total * 100
                )
            )  # amount in cents
        context.user_data["prices"] = (
            prices  # store prices so we can use them in pre_checkout_query
        )

        invoice_payload = self.get_invoice_payload(telegram_user_id, cart)

        # Another option: redirect to URL for payment

        await query.bot.send_invoice(
            chat_id=query.message.chat_id,
            title=_("Your Order"),
            description=_("Payment for your order."),
            payload=invoice_payload,  # unique payload
            provider_token=self.payment_provider_token,
            currency=self.currency,
            prices=prices,
            start_parameter="start_parameter",
            need_shipping_address=self.payment_need_shipping_address,
            need_name=self.payment_need_name,
            need_phone_number=self.payment_need_phone_number,
            need_email=self.payment_need_email,
            is_flexible=False,
        )

    def get_invoice_payload(self, telegram_user_id, cart):
        return "some-invoice-payload"

    async def _pre_checkout_query(
        self, update: Update, context: CallbackContext
    ) -> None:
        """
        * This function handles pre-checkout queries, which are sent by Telegram to verify the payment details.
        * It checks if the total amount matches the expected amount.
        * It answers the query with ok=True or ok=False based on the verification result.
        """

        query = update.pre_checkout_query
        if query.total_amount != sum(
            price.amount for price in context.user_data["prices"]
        ):
            await query.answer(ok=False, error_message="Price mismatch")
        else:
            await query.answer(ok=True)

    async def _successful_payment(
        self, update: Update, context: CallbackContext
    ) -> None:
        """
        * This function is called when the payment is successful.
        * It sends a confirmation message and clears the user'sawait cart.
        """

        await update.message.reply_text(
            _("Payment successful! Thank you for your purchase.")
        )
        telegram_user_id = update.effective_user.id

        cart = self.ecommerce.get_cart(telegram_user_id)
        await cart.clear()

        await self._show_main_menu(update, context, edit_previous_message=False)

    async def _get_recommendations(
        self, update: Update, context: CallbackContext
    ) -> None:
        await update.message.reply_text(
            _(
                "Please tell me what you are looking for, and I will recommend some products:"
            ),
            reply_markup=ForceReply(selective=True),
        )
        return (
            self.EcommerceTelegramBotState.RECOMMENDATIONS
        )  # Wait for user's description

    async def _get_relevant_products(self, user_recommendation_request):
        # TODO: RAG system indexing the title and description of the product
        products = await self.ecommerce.get_all_products()
        return products

    async def _create_specifications_of_relevant_products(
        self, user_recommendation_request
    ):
        # NOTE: Another option is to use tabulate python library and print the products in a table with github format
        products = await self._get_relevant_products(user_recommendation_request)

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
        product_specifications = await self._create_specifications_of_relevant_products(
            user_recommendation_request
        )

        try:
            response = await litellm.acompletion(
                model=self.llm_model,
                messages=[
                    {
                        "role": "user",
                        "content": f"""These are the available relevant products: 
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
{user_recommendation_request}""",
                    },
                ],
                response_format={"type": "json_object"},
                temperature=self.llm_temperature,
            )
            recommendations = json.loads(response.choices[0].message.content.strip())
            return recommendations["products"]
        except Exception as e:
            logger.error(f"Error generating recommendations: {e}")
            return None

    async def _handle_request_for_product_recommendations(
        self, update: Update, context: CallbackContext
    ) -> None:
        # TODO: Navigation to product detail
        user_recommendation_request = update.message.text
        recommendations = await self._generate_recommendations(
            user_recommendation_request
        )
        if recommendations:
            recommendation_text = _("Here there are some recommendations:\n")
            for product in recommendations:
                recommendation_text += f"- {product['id']}: {product['name']}\n"
            await update.message.reply_text(recommendation_text)
        else:
            await update.message.reply_text(
                _("Sorry, I couldn't find any recommendations.")
            )

        return ConversationHandler.END

    async def _handle_user_text(self, update: Update, context: CallbackContext) -> None:
        await self._handle_search_products(update=update, context=context)

    def create_product_carousel_inline_markup(self, product, product_image_index=0):
        num_product_image_urls = len(product.image_urls)

        carousel_buttons_navigation = [
            InlineKeyboardButton(
                _("Previous"),
                callback_data=f"change_product_carousel_image:{product.id}:{(product_image_index - 1) % num_product_image_urls}",
            ),
            InlineKeyboardButton(
                _("Next"),
                callback_data=f"change_product_carousel_image:{product.id}:{(product_image_index + 1) % num_product_image_urls}",
            ),
        ]

        return InlineKeyboardMarkup([carousel_buttons_navigation])

    async def _handle_change_product_carousel_image(
        self, update: Update, context: CallbackContext
    ) -> None:
        query = update.callback_query
        await query.answer()

        product_id, product_image_index = query.data.split(":")[1:]
        product_id = int(product_id)
        product_image_index = int(product_image_index)

        product = await self.ecommerce.get_product(product_id)

        num_product_photos = len(product.image_urls)

        product_image_index = max(
            0, min(product_image_index, num_product_photos - 1)
        )  # Ensure index is in range.

        if product is None:
            await query.message.reply_text(
                _("Product does not exist: #{product_id} {product_name}").format(
                    product_id=product.id, product_name=product.name
                )
            )
            return

        reply_markup = self.create_product_carousel_inline_markup(
            product, product_image_index=product_image_index
        )

        media = InputMediaPhoto(media=product.image_urls[product_image_index])
        await query.edit_message_media(media=media, reply_markup=reply_markup)
        await query.edit_message_caption(
            caption=_("Photo")
            + " %s/%s" % (product_image_index + 1, num_product_photos),
            reply_markup=reply_markup,
        )

    async def _show_product(self, update: Update, context: CallbackContext) -> None:
        query = update.callback_query
        await query.answer()

        product_id = query.data.split(":")[1]
        product_id = int(product_id)

        product = await self.ecommerce.get_product(product_id)
        if product is None:
            await query.reply_text(
                _("Product does not exist: #{product_id}").format(
                    product_id=product_id
                ),
                chat_id=query.message.chat_id,
            )
            return

        telegram_user_id = query.from_user.id
        chat_id = query.message.chat_id
        money = await self.ecommerce.get_money_locale(telegram_user_id)

        localized_price = money.format_price(product.price)

        product_category_id = product.category_id

        category = await self.ecommerce.get_category(product_category_id)
        product_category_name = category.name

        cart = self.ecommerce.get_cart(telegram_user_id)
        num_products_in_cart = await cart.get_num_products()

        # TODO: Show category breadcrumb
        product_text = (
            f"*{product.name}*\n\n{product.description}\n\nPrice: {localized_price}"
        )

        product_reply_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        _("Add to Cart"),
                        callback_data=f"add_to_cart:{product_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        (
                            _("Cart ({num_products_in_cart})").format(
                                num_products_in_cart=num_products_in_cart
                            )
                            if num_products_in_cart != 0
                            else _("Cart")
                        ),
                        callback_data="cart",
                    )
                ],
                [
                    InlineKeyboardButton(
                        _("Category {product_category_name}").format(
                            product_category_name=product_category_name
                        ),
                        callback_data=f"category:{product_category_id}:1",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        _("Back to Categories"),
                        callback_data="show_categories_in_new_message",
                    )
                ],
            ]
        )

        if product.image_urls is not None and len(product.image_urls) > 0:
            if (
                self.product_detail_image_view_type
                == self.ProductDetailImageViewType.MAIN_PHOTO
                or len(product.image_urls) == 1
            ):

                await context.bot.send_photo(
                    photo=product.image_urls[0],
                    caption=product_text,
                    reply_markup=product_reply_markup,
                    parse_mode=ParseMode.MARKDOWN,
                    chat_id=chat_id,
                )
            else:
                if (
                    self.product_detail_image_view_type
                    == self.ProductDetailImageViewType.IMAGE_GALLERY
                ):
                    # Image gallery
                    media = [InputMediaPhoto(media=url) for url in product.image_urls]
                    await context.bot.send_media_group(
                        media=media,
                        chat_id=chat_id,
                        caption=_("Photo gallery of {product_name}").format(
                            product_name=product.name
                        ),
                    )
                else:
                    # Carousel
                    carousel_reply_markup = self.create_product_carousel_inline_markup(
                        product, 0
                    )
                    await context.bot.send_photo(
                        photo=product.image_urls[0],
                        reply_markup=carousel_reply_markup,
                        chat_id=chat_id,
                        caption=_("Photo") + " 1/%s" % len(product.image_urls),
                    )

                await context.bot.send_message(
                    text=product_text,
                    reply_markup=product_reply_markup,
                    parse_mode=ParseMode.MARKDOWN,
                    chat_id=chat_id,
                )
        else:
            await context.bot.send_message(
                product_text,
                reply_markup=product_reply_markup,
                parse_mode=ParseMode.MARKDOWN,
                chat_id=chat_id,
            )

    async def _post_init(self, application: ApplicationBuilder):
        bot = application.bot
        await bot.set_my_commands(
            [
                BotCommand("categories", _("Categories")),
                BotCommand("cart", _("Cart")),
                BotCommand("recommendations", _("Recommendations")),
                BotCommand("search", _("Search")),
                BotCommand("orders", _("Orders")),
                BotCommand("account", _("Account")),
            ]
        )

    def run(self):
        self.application.run_polling(allowed_updates=Update.ALL_TYPES)


def main() -> None:
    ecommerce = EcommerceDemo()

    bot = EcommerceTelegramBot(
        token=BOT_TOKEN,
        ecommerce=ecommerce,
        payment_provider_token=PAYMENT_PROVIDER_TOKEN,
    )
    bot.run()


if __name__ == "__main__":
    main()
