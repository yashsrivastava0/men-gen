"""Test helpers: a deterministic, valid-by-construction sample app (commerce-like).

``make_sample_spec(n_screens, seed=seed)`` returns an ``AppSpec`` with exactly ``n_screens``
screens (1 and up; 50+ is fine) that passes all nine validators with ``make_sample_stack_profile()``
and the default ``Limits``. The same arguments always give the same spec, and so the same bytes
once rendered. ``seed`` varies the app name, tone, brand colour and the order in which optional
modules (wishlist, coupons, stores, ...) are added after the 19 core screens.

Used by the spec-builder and spec-loader tests and, later, by the builder and test engineers.
"""

from __future__ import annotations

import colorsys
import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from mde_contracts.content import Capability, StackProfile, UnsupportedType
from mde_contracts.research import SourceRef
from mde_contracts.spec import (
    AcceptanceBlock,
    ApiBlock,
    ApiOperation,
    AppBlock,
    AppSpec,
    AssumptionItem,
    AssumptionsBlock,
    BehaviorBlock,
    BuildSequence,
    DataBinding,
    DataBlock,
    DesignBlock,
    DesignTokens,
    Entity,
    EnumDef,
    FieldDef,
    FieldType,
    FlowBlock,
    Integration,
    IntegrationsBlock,
    NavigationBlock,
    NavNode,
    Permission,
    ResearchAddition,
    RevisionBlock,
    Role,
    RolesBlock,
    ScreenAction,
    ScreenBlock,
    SpecHeader,
    StateMachine,
    Transition,
    TypeStyle,
)
from mde_core.hashing import sha256_hex
from mde_spec_builder.assemble import assign_flow_ids, assign_screen_ids, number_navigation
from mde_spec_builder.render import with_rendered_hash
from mde_spec_builder.sequence import build_sequence

_Act = tuple[str, str | None, str | None, str | None]  # name, calls, go ("@key"), on_success
_Bind = tuple[str, tuple[str, ...]]  # bind, show

CORE_SCREEN_COUNT = 19


def make_sample_stack_profile() -> StackProfile:
    """A small Stack Profile for tests (push and biometrics need a development build)."""
    return StackProfile(
        id="expo-managed",
        label="Expo managed (test profile)",
        capabilities=[
            Capability(id="camera", label="Camera", keywords=["camera", "photo capture"]),
            Capability(id="location", label="Location", keywords=["location", "gps"]),
            Capability(
                id="push",
                label="Push notifications",
                needs_dev_build=True,
                preview_limit="Push does not work in the web preview.",
                keywords=["push", "push notification"],
            ),
            Capability(id="payments", label="Payments", keywords=["payment", "payments"]),
            Capability(
                id="biometrics",
                label="Biometric login",
                needs_dev_build=True,
                keywords=["biometric", "face id", "fingerprint"],
            ),
            Capability(
                id="bluetooth", label="Bluetooth", supported=False, keywords=["bluetooth", "ble"]
            ),
        ],
        unsupported_types=[
            UnsupportedType(
                id="game",
                label="Games",
                keywords=["game", "multiplayer"],
                reason="Games need a game engine.",
                closest_option="a loyalty or rewards app",
            ),
            UnsupportedType(
                id="ar",
                label="Augmented reality",
                keywords=["augmented reality", "ar"],
                reason="AR needs native modules.",
                closest_option="a product gallery",
            ),
        ],
    )


# --------------------------------------------------------------------------- screen templates


@dataclass(frozen=True)
class _Tpl:
    key: str
    route: str
    recipe: str
    purpose: str
    parent: str | None = None
    nav: str = "stack"  # tab | stack | auth
    label: str | None = None
    flow: str | None = None
    surface: str = "customer"
    auth: str = "public"
    data: tuple[_Bind, ...] = ()
    sections: tuple[str, ...] = ()
    actions: tuple[_Act, ...] = ()
    states: dict[str, dict[str, str]] = field(default_factory=dict)
    texts: dict[str, str] = field(default_factory=dict)
    checks: tuple[str, ...] = ()
    research_ref: str | None = None


def _core_templates() -> list[_Tpl]:
    return [
        _Tpl(
            "home",
            "/",
            "home-feed",
            "Welcome the shopper and surface featured products",
            nav="tab",
            label="Home",
            flow="browse",
            data=(("API-listProducts(featured)", ("name", "images", "price")),),
            sections=("hero", "categories_row", "featured_list"),
            actions=(("open_product", None, "@product", None), ("search", None, "@search", None)),
            texts={"title": "Today", "cta": "Shop now"},
            checks=("Featured products load on first open", "Tapping a product opens its detail"),
        ),
        _Tpl(
            "catalog",
            "/catalog",
            "list-with-filters",
            "Browse every product with filters",
            nav="tab",
            label="Shop",
            flow="browse",
            data=(
                ("API-listCategories()", ("name", "image")),
                ("API-listProducts(category,page)", ("name", "images", "price", "rating")),
            ),
            sections=("category_chips", "product_grid", "filter_sheet"),
            actions=(("open_product", None, "@product", None),),
            texts={"title": "Shop", "empty": "No products match these filters"},
            checks=(
                "Choosing a category narrows the grid",
                "An empty result shows the empty state",
            ),
        ),
        _Tpl(
            "product",
            "/product/[id]",
            "detail-with-sticky-cta",
            "Show one product and let the shopper add it to the cart",
            parent="catalog",
            flow="browse",
            data=(
                (
                    "API-getProduct(id)",
                    ("name", "images", "price", "rating", "description", "stock"),
                ),
                ("API-listReviews(productId)", ("rating", "body")),
            ),
            sections=("gallery", "title_price", "description", "reviews_preview"),
            actions=(
                ("add_to_cart", "API-addCartItem", None, "toast + cart badge"),
                ("see_reviews", None, "@reviews", None),
            ),
            states={"out_of_stock": {"disable": "add_to_cart", "show": "notify_me"}},
            texts={"cta": "Add to cart"},
            checks=(
                "Add to cart increments the cart badge",
                "An out-of-stock item disables the button",
            ),
        ),
        _Tpl(
            "cart",
            "/cart",
            "cart-list",
            "Review the items and change quantities before paying",
            nav="tab",
            label="Cart",
            flow="browse",
            data=(("API-getCart()", ("items", "total")),),
            sections=("item_list", "promo_code", "summary_bar"),
            actions=(
                ("update_quantity", "API-updateCartItem", None, None),
                ("remove_item", "API-removeCartItem", None, "toast"),
                ("checkout", None, "@checkout", None),
            ),
            texts={"title": "Your cart", "empty": "Your cart is empty"},
            checks=(
                "Changing a quantity updates the total",
                "Removing the last item shows the empty state",
            ),
        ),
        _Tpl(
            "checkout",
            "/checkout",
            "checkout-form",
            "Collect the address and pay for the order",
            parent="cart",
            flow="browse",
            auth="authenticated",
            data=(("API-listAddresses()", ("line1", "city", "is_default")),),
            sections=("address_picker", "payment_method", "order_summary"),
            actions=(("place_order", "API-checkout", None, "@order_done"),),
            texts={"cta": "Place order"},
            checks=(
                "A missing address blocks the order",
                "A successful order opens the confirmation",
            ),
        ),
        _Tpl(
            "order_done",
            "/checkout/done",
            "confirmation",
            "Confirm that the order was placed",
            parent="checkout",
            flow="browse",
            auth="authenticated",
            data=(("ENT-Order.status", ("status", "total")),),
            sections=("success_banner", "order_number", "next_steps"),
            actions=(
                ("view_order", None, "@order_detail", None),
                ("keep_shopping", None, "@home", None),
            ),
            texts={"title": "Thank you"},
            checks=("The order number is shown",),
        ),
        _Tpl(
            "search",
            "/search",
            "search",
            "Find products by typing",
            parent="home",
            flow="browse",
            data=(("API-searchProducts(q)", ("name", "images", "price")),),
            sections=("search_bar", "recent_searches", "results_list"),
            actions=(("open_product", None, "@product", None),),
            texts={"placeholder": "Search products"},
            checks=("Results update after the shopper stops typing",),
        ),
        _Tpl(
            "login",
            "/login",
            "auth-form",
            "Let a returning customer sign in",
            nav="auth",
            flow="signin",
            sections=("email_field", "secret_field", "submit_button", "register_link"),
            actions=(
                ("sign_in", "API-login", None, "@home"),
                ("create_account", None, "@register", None),
            ),
            texts={"email_label": "Email", "secret_label": "Your key", "cta": "Sign in"},
            checks=("A wrong email or key shows an inline error",),
        ),
        _Tpl(
            "register",
            "/register",
            "auth-form",
            "Let a new customer create an account",
            nav="auth",
            flow="signin",
            sections=("name_field", "email_field", "secret_field", "submit_button"),
            actions=(
                ("create_account", "API-register", None, "@home"),
                ("have_account", None, "@login", None),
            ),
            texts={"cta": "Create account"},
            checks=("An email already in use shows an inline error",),
        ),
        _Tpl(
            "orders",
            "/orders",
            "list",
            "List the shopper's past orders",
            nav="tab",
            label="Orders",
            flow="orders",
            auth="authenticated",
            data=(("API-listOrders()", ("status", "total", "placed_at")),),
            sections=("order_list",),
            actions=(("open_order", None, "@order_detail", None),),
            texts={"title": "Orders", "empty": "No orders yet"},
            checks=("Newest orders come first",),
        ),
        _Tpl(
            "order_detail",
            "/orders/[id]",
            "detail",
            "Show one order and where it is now",
            parent="orders",
            flow="orders",
            auth="authenticated",
            data=(("API-getOrder(id)", ("status", "total", "placed_at")),),
            sections=("status_timeline", "items", "address", "totals"),
            states={"cancelled": {"hide": "status_timeline"}},
            texts={"title": "Order"},
            checks=("The timeline matches the order status",),
            research_ref="RA-001",
        ),
        _Tpl(
            "profile",
            "/profile",
            "profile",
            "Show the account and link to its settings",
            nav="tab",
            label="Profile",
            flow="account",
            auth="authenticated",
            data=(("API-getProfile()", ("name", "email", "avatar")),),
            sections=("header", "menu_list"),
            actions=(
                ("edit_profile", None, "@edit_profile", None),
                ("manage_addresses", None, "@addresses", None),
                ("open_settings", None, "@settings", None),
                ("open_notifications", None, "@notifications", None),
            ),
            texts={"title": "Profile"},
            checks=("Every menu row opens its screen",),
        ),
        _Tpl(
            "edit_profile",
            "/profile/edit",
            "form",
            "Change the name, phone and photo",
            parent="profile",
            flow="account",
            auth="authenticated",
            data=(("API-getProfile()", ("name", "phone", "avatar")),),
            sections=("name_field", "phone_field", "photo_picker"),
            actions=(("save", "API-updateProfile", None, "toast"),),
            texts={"cta": "Save"},
            checks=("Saving shows a confirmation toast",),
        ),
        _Tpl(
            "addresses",
            "/profile/addresses",
            "list",
            "Manage saved delivery addresses",
            parent="profile",
            flow="account",
            auth="authenticated",
            data=(("API-listAddresses()", ("line1", "city", "is_default")),),
            sections=("address_list",),
            actions=(("add_address", None, "@address_form", None),),
            texts={"title": "Addresses", "empty": "No saved addresses"},
            checks=("The default address is marked",),
        ),
        _Tpl(
            "address_form",
            "/profile/addresses/new",
            "form",
            "Add a delivery address",
            parent="addresses",
            flow="account",
            auth="authenticated",
            sections=("line_fields", "default_toggle"),
            actions=(("save", "API-createAddress", None, "toast"),),
            texts={"cta": "Save address"},
            checks=("A missing postal code blocks saving",),
        ),
        _Tpl(
            "settings",
            "/settings",
            "settings",
            "Change language, theme and sign out",
            parent="profile",
            flow="account",
            auth="authenticated",
            sections=("language_row", "theme_row", "sign_out_row"),
            texts={"title": "Settings"},
            checks=("Signing out returns to the sign-in screen",),
        ),
        _Tpl(
            "notifications",
            "/notifications",
            "notifications",
            "List the messages sent to the shopper",
            parent="profile",
            auth="authenticated",
            data=(("API-listNotifications()", ("title", "body", "read")),),
            sections=("notification_list",),
            texts={"title": "Messages", "empty": "No messages yet"},
            checks=("Unread messages are highlighted",),
        ),
        _Tpl(
            "reviews",
            "/product/[id]/reviews",
            "list",
            "Read what other shoppers think of a product",
            parent="product",
            flow="reviews",
            data=(("API-listReviews(productId)", ("rating", "body")),),
            sections=("rating_summary", "review_list"),
            actions=(("write_review", None, "@write_review", None),),
            texts={"title": "Reviews", "empty": "Be the first to review"},
            checks=("Reviews show newest first",),
        ),
        _Tpl(
            "write_review",
            "/product/[id]/review/new",
            "form",
            "Let a shopper rate and review a product",
            parent="reviews",
            flow="reviews",
            auth="authenticated",
            sections=("star_picker", "body_field"),
            actions=(("submit", "API-createReview", None, "toast"),),
            texts={"cta": "Post review"},
            checks=("A review needs a star rating",),
        ),
    ]


@dataclass(frozen=True)
class _Module:
    key: str
    entity: str
    plain: str
    fields: tuple[tuple[str, FieldType, str | None], ...]  # name, type, ref entity or enum name
    flow_name: str


_MODULES: tuple[_Module, ...] = (
    _Module(
        "wishlist",
        "Wishlist",
        "Wishlist items",
        (
            ("product", "ref", "ENT-Product"),
            ("added_at", "datetime", None),
            ("note", "string", None),
        ),
        "Wishlist",
    ),
    _Module(
        "coupons",
        "Coupon",
        "Coupons",
        (
            ("code", "string", None),
            ("percent", "int", None),
            ("expires", "date", None),
            ("description", "string", None),
        ),
        "Coupons",
    ),
    _Module(
        "stores",
        "Store",
        "Stores",
        (
            ("name", "string", None),
            ("address", "text", None),
            ("phone", "string", None),
            ("opens", "string", None),
        ),
        "Stores",
    ),
    _Module(
        "brands",
        "Brand",
        "Brands",
        (("name", "string", None), ("logo", "image", None), ("description", "text", None)),
        "Brands",
    ),
    _Module(
        "tickets",
        "Ticket",
        "Support tickets",
        (
            ("subject", "string", None),
            ("body", "text", None),
            ("status", "enum", "TicketStatus"),
            ("created_at", "datetime", None),
        ),
        "Support",
    ),
    _Module(
        "articles",
        "Article",
        "Help articles",
        (("title", "string", None), ("body", "text", None), ("topic", "string", None)),
        "Help center",
    ),
    _Module(
        "plans",
        "Subscription",
        "Subscriptions",
        (
            ("plan", "string", None),
            ("price", "money", None),
            ("renews_on", "date", None),
            ("active", "bool", None),
        ),
        "Subscriptions",
    ),
    _Module(
        "returns",
        "Return",
        "Returns",
        (
            ("order", "ref", "ENT-Order"),
            ("reason", "text", None),
            ("status", "enum", "ReturnStatus"),
        ),
        "Returns",
    ),
)

_MODULE_ENUMS: dict[str, list[str]] = {
    "TicketStatus": ["open", "answered", "closed"],
    "ReturnStatus": ["requested", "approved", "received", "refunded"],
}


def _module_templates(module: _Module) -> list[_Tpl]:
    key, ent = module.key, module.entity
    names = [name for name, _t, _r in module.fields]
    shown = tuple(names[:3])
    detail = tuple(names[:4])
    return [
        _Tpl(
            f"{key}_list",
            f"/{key}",
            "list",
            f"List {module.plain.lower()}",
            parent="profile",
            flow=key,
            auth="authenticated",
            data=((f"API-list{ent}()", shown),),
            sections=("item_list",),
            actions=(
                ("open_item", None, f"@{key}_detail", None),
                ("add_new", None, f"@{key}_form", None),
            ),
            texts={"title": module.plain, "empty": f"No {module.plain.lower()} yet"},
            checks=(f"Tapping a row opens the {ent.lower()}",),
        ),
        _Tpl(
            f"{key}_detail",
            f"/{key}/[id]",
            "detail",
            f"Show one {ent.lower()}",
            parent=f"{key}_list",
            flow=key,
            auth="authenticated",
            data=((f"API-get{ent}(id)", detail),),
            sections=("summary", "details"),
            texts={"title": ent},
            checks=(f"All {ent.lower()} fields are shown",),
        ),
        _Tpl(
            f"{key}_form",
            f"/{key}/new",
            "form",
            f"Create a {ent.lower()}",
            parent=f"{key}_list",
            flow=key,
            auth="authenticated",
            sections=("fields",),
            actions=(("save", f"API-create{ent}", None, "toast"),),
            texts={"cta": "Save"},
            checks=("Required fields block saving",),
        ),
        _Tpl(
            f"{key}_history",
            f"/{key}/history",
            "list",
            f"Past {module.plain.lower()}",
            parent=f"{key}_list",
            flow=key,
            auth="authenticated",
            data=((f"API-list{ent}(since)", shown),),
            sections=("item_list",),
            actions=(("open_item", None, f"@{key}_detail", None),),
            texts={"title": f"History: {module.plain}"},
            checks=("Older entries load as the shopper scrolls",),
        ),
    ]


def _admin_templates(count: int) -> list[_Tpl]:
    """Admin dashboard plus ``count - 1`` report screens (unlimited, for very large samples)."""
    out = [
        _Tpl(
            "admin_dashboard",
            "/admin",
            "dashboard",
            "Give the shop owner today's numbers",
            parent="settings",
            flow="admin",
            surface="admin",
            auth="ROLE-admin",
            data=(("API-listAllOrders(today)", ("status", "total")),),
            sections=("kpi_row", "recent_orders"),
            actions=(("open_report", None, "@report_1", None),),
            texts={"title": "Dashboard"},
            checks=("Only an admin can open the dashboard",),
        )
    ]
    for number in range(1, count):
        parent = "admin_dashboard" if number == 1 else f"report_{number - 1}"
        out.append(
            _Tpl(
                f"report_{number}",
                f"/admin/reports/{number}",
                "list",
                f"Report {number}: orders by status",
                parent=parent,
                flow="admin",
                surface="admin",
                auth="ROLE-admin",
                data=((f"API-listAllOrders(report{number})", ("status", "total", "placed_at")),),
                sections=("filters", "table"),
                actions=(("next_report", None, f"@report_{number + 1}", None),),
                texts={"title": f"Report {number}"},
                checks=("Filters change the rows",),
            )
        )
    return out


def _select_templates(n_screens: int, rng: random.Random) -> list[_Tpl]:
    templates = _core_templates()
    modules = list(_MODULES)
    rng.shuffle(modules)
    for module in modules:
        templates.extend(_module_templates(module))
    extra = max(n_screens - len(templates), 0)
    if extra:
        templates.extend(_admin_templates(extra))
    return templates[:n_screens]


# --------------------------------------------------------------------------- data and api


def _f(
    name: str,
    ftype: FieldType = "string",
    *,
    required: bool = True,
    ref: str | None = None,
    enum: str | None = None,
) -> FieldDef:
    return FieldDef(name=name, type=ftype, required=required, ref=ref, enum=enum)


def _core_entities() -> list[Entity]:
    transitions = [
        Transition(source="placed", target="paid", on="pay"),
        Transition(source="paid", target="shipped", on="ship"),
        Transition(source="shipped", target="delivered", on="deliver"),
        Transition(source="placed", target="cancelled", on="cancel"),
        Transition(source="paid", target="cancelled", on="cancel"),
    ]
    states = ["placed", "paid", "shipped", "delivered", "cancelled"]
    return [
        Entity(
            id="ENT-User",
            name="User",
            plain_name="Customers",
            fields=[
                _f("name"),
                _f("email"),
                _f("phone", required=False),
                _f("avatar", "image", required=False),
            ],
        ),
        Entity(
            id="ENT-Category",
            name="Category",
            plain_name="Categories",
            fields=[_f("name"), _f("image", "image", required=False), _f("sort_order", "int")],
        ),
        Entity(
            id="ENT-Product",
            name="Product",
            plain_name="Products",
            fields=[
                _f("name"),
                _f("description", "text"),
                _f("price", "money"),
                _f("images", "image"),
                _f("rating", "number"),
                _f("category", "ref", ref="ENT-Category"),
                _f("stock", "int"),
                _f("status", "enum", enum="ProductStatus"),
            ],
            seed_guidance="12 products across 4 categories, prices between 5 and 120",
        ),
        Entity(
            id="ENT-Cart",
            name="Cart",
            plain_name="Carts",
            fields=[
                _f("user", "ref", ref="ENT-User", required=False),
                _f("items", "json"),
                _f("total", "money"),
            ],
        ),
        Entity(
            id="ENT-CartItem",
            name="CartItem",
            plain_name="Cart items",
            fields=[
                _f("product", "ref", ref="ENT-Product"),
                _f("quantity", "int"),
                _f("unit_price", "money"),
            ],
        ),
        Entity(
            id="ENT-Order",
            name="Order",
            plain_name="Orders",
            fields=[
                _f("user", "ref", ref="ENT-User"),
                _f("status", "enum", enum="OrderStatus"),
                _f("total", "money"),
                _f("placed_at", "datetime"),
                _f("address", "ref", ref="ENT-Address"),
            ],
            state_machine=StateMachine(field="status", states=states, transitions=transitions),
        ),
        Entity(
            id="ENT-OrderItem",
            name="OrderItem",
            plain_name="Order lines",
            fields=[
                _f("order", "ref", ref="ENT-Order"),
                _f("product", "ref", ref="ENT-Product"),
                _f("quantity", "int"),
                _f("unit_price", "money"),
            ],
        ),
        Entity(
            id="ENT-Address",
            name="Address",
            plain_name="Addresses",
            fields=[
                _f("line1"),
                _f("city"),
                _f("postal_code"),
                _f("country"),
                _f("is_default", "bool"),
            ],
        ),
        Entity(
            id="ENT-Review",
            name="Review",
            plain_name="Reviews",
            fields=[
                _f("product", "ref", ref="ENT-Product"),
                _f("user", "ref", ref="ENT-User"),
                _f("rating", "int"),
                _f("body", "text", required=False),
            ],
        ),
        Entity(
            id="ENT-Notification",
            name="Notification",
            plain_name="Messages",
            fields=[
                _f("user", "ref", ref="ENT-User"),
                _f("title"),
                _f("body", "text"),
                _f("read", "bool"),
                _f("created_at", "datetime"),
            ],
        ),
    ]


def _module_entity(module: _Module) -> Entity:
    fields: list[FieldDef] = []
    for name, ftype, extra in module.fields:
        if ftype == "ref":
            fields.append(_f(name, "ref", ref=extra))
        elif ftype == "enum":
            fields.append(_f(name, "enum", enum=extra))
        else:
            fields.append(_f(name, ftype))
    return Entity(
        id=f"ENT-{module.entity}", name=module.entity, plain_name=module.plain, fields=fields
    )


def _op(
    op_id: str,
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"],
    path: str,
    *,
    req: str | None = None,
    res: str | None = None,
    many: bool = False,
    auth: str = "public",
    roles: Sequence[str] = (),
) -> ApiOperation:
    return ApiOperation(
        id=op_id,
        name=op_id.removeprefix("API-"),
        method=method,
        path=path,
        request_entity=req,
        response_entity=res,
        response_many=many,
        auth=auth,
        roles=list(roles),
    )


def _core_operations() -> list[ApiOperation]:
    return [
        _op("API-listCategories", "GET", "/categories", res="ENT-Category", many=True),
        _op("API-listProducts", "GET", "/products", res="ENT-Product", many=True),
        _op("API-searchProducts", "GET", "/search", res="ENT-Product", many=True),
        _op("API-getProduct", "GET", "/products/{id}", res="ENT-Product"),
        _op("API-listReviews", "GET", "/products/{id}/reviews", res="ENT-Review", many=True),
        _op(
            "API-createReview",
            "POST",
            "/products/{id}/reviews",
            req="ENT-Review",
            res="ENT-Review",
            auth="authenticated",
        ),
        _op("API-getCart", "GET", "/cart", res="ENT-Cart"),
        _op("API-addCartItem", "POST", "/cart/items", req="ENT-CartItem", res="ENT-Cart"),
        _op("API-updateCartItem", "PATCH", "/cart/items/{id}", req="ENT-CartItem", res="ENT-Cart"),
        _op("API-removeCartItem", "DELETE", "/cart/items/{id}", res="ENT-Cart"),
        _op(
            "API-checkout",
            "POST",
            "/orders",
            req="ENT-Order",
            res="ENT-Order",
            auth="authenticated",
        ),
        _op("API-listOrders", "GET", "/orders", res="ENT-Order", many=True, auth="authenticated"),
        _op("API-getOrder", "GET", "/orders/{id}", res="ENT-Order", auth="authenticated"),
        _op("API-login", "POST", "/auth/login", req="ENT-User", res="ENT-User"),
        _op("API-register", "POST", "/auth/register", req="ENT-User", res="ENT-User"),
        _op("API-getProfile", "GET", "/me", res="ENT-User", auth="authenticated"),
        _op(
            "API-updateProfile",
            "PATCH",
            "/me",
            req="ENT-User",
            res="ENT-User",
            auth="authenticated",
        ),
        _op(
            "API-listAddresses",
            "GET",
            "/me/addresses",
            res="ENT-Address",
            many=True,
            auth="authenticated",
        ),
        _op(
            "API-createAddress",
            "POST",
            "/me/addresses",
            req="ENT-Address",
            res="ENT-Address",
            auth="authenticated",
        ),
        _op(
            "API-listNotifications",
            "GET",
            "/me/notifications",
            res="ENT-Notification",
            many=True,
            auth="authenticated",
        ),
        _op(
            "API-listAllOrders",
            "GET",
            "/admin/orders",
            res="ENT-Order",
            many=True,
            auth="ROLE-admin",
            roles=["ROLE-admin"],
        ),
        _op(
            "API-updateOrderStatus",
            "PATCH",
            "/admin/orders/{id}",
            req="ENT-Order",
            res="ENT-Order",
            auth="ROLE-admin",
            roles=["ROLE-admin"],
        ),
    ]


def _module_operations(module: _Module) -> list[ApiOperation]:
    ent, key = module.entity, module.key
    return [
        _op(f"API-list{ent}", "GET", f"/{key}", res=f"ENT-{ent}", many=True, auth="authenticated"),
        _op(f"API-get{ent}", "GET", f"/{key}/{{id}}", res=f"ENT-{ent}", auth="authenticated"),
        _op(
            f"API-create{ent}",
            "POST",
            f"/{key}",
            req=f"ENT-{ent}",
            res=f"ENT-{ent}",
            auth="authenticated",
        ),
    ]


# --------------------------------------------------------------------------- design


def _hex(red: float, green: float, blue: float) -> str:
    return f"#{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}"


def _design(rng: random.Random) -> DesignBlock:
    hue = rng.random()
    primary = _hex(*colorsys.hsv_to_rgb(hue, 0.65, 0.72))
    primary_dark = _hex(*colorsys.hsv_to_rgb(hue, 0.45, 0.92))
    light = {
        "background": "#FFFFFF", "surface": "#F6F7F9", "primary": primary, "text": "#14171A",
        "text_muted": "#5B6470", "text_on_primary": "#FFFFFF", "border": "#DDE1E6",
        "success": "#1E8E5A", "warning": "#B7791F", "danger": "#C0392B",
    }  # fmt: skip
    dark = {
        "background": "#101317", "surface": "#1A1F25", "primary": primary_dark, "text": "#F2F4F6",
        "text_muted": "#A3ACB8", "text_on_primary": "#101317", "border": "#2B323B",
        "success": "#4CC38A", "warning": "#E0A94B", "danger": "#F0786A",
    }  # fmt: skip
    return DesignBlock(
        direction="clean-bright",
        tokens=DesignTokens(
            colors_light=light,
            colors_dark=dark,
            type_scale={
                "display": TypeStyle(size=32, weight=700, line_height=1.2),
                "title": TypeStyle(size=22, weight=600),
                "body": TypeStyle(size=16, line_height=1.5),
                "caption": TypeStyle(size=12),
            },
            spacing=[4, 8, 12, 16, 24, 32],
            radius={"sm": 6, "md": 12, "lg": 20},
            elevation={"low": "0 1px 2px rgba(0,0,0,0.08)", "high": "0 8px 24px rgba(0,0,0,0.16)"},
            motion={"fast": "120ms ease-out", "normal": "220ms ease-in-out"},
        ),
        nav_style="bottom-tabs",
        imagery_rules=["Product photos on a plain background", "No stock people"],
        component_variants={
            "button": ["primary", "secondary", "ghost"],
            "card": ["flat", "raised"],
        },
    )


# --------------------------------------------------------------------------- navigation


def _children(templates: Sequence[_Tpl], key: str) -> list[_Tpl]:
    return [t for t in templates if t.parent == key]


def _nav_node(tpl: _Tpl, templates: Sequence[_Tpl], ids: dict[str, str]) -> NavNode:
    return NavNode(
        id="NAV-001",
        route=tpl.route,
        kind="screen",
        screen=ids[tpl.key],
        label=tpl.label,
        auth=tpl.auth,
        children=[_nav_node(c, templates, ids) for c in _children(templates, tpl.key)],
    )


def _nav_order(templates: Sequence[_Tpl]) -> list[str]:
    order: list[str] = []

    def visit(tpl: _Tpl) -> None:
        order.append(tpl.key)
        for child in _children(templates, tpl.key):
            visit(child)

    for tpl in templates:
        if tpl.nav == "auth":
            visit(tpl)
    for tpl in templates:
        if tpl.nav == "tab":
            visit(tpl)
    return order


def _navigation(templates: Sequence[_Tpl], ids: dict[str, str]) -> NavigationBlock:
    groups: list[NavNode] = []
    auth_tops = [t for t in templates if t.nav == "auth"]
    tab_tops = [t for t in templates if t.nav == "tab"]
    if auth_tops:
        groups.append(
            NavNode(
                id="NAV-001",
                route="/(auth)",
                kind="auth_gate",
                children=[_nav_node(t, templates, ids) for t in auth_tops],
            )
        )
    if tab_tops:
        groups.append(
            NavNode(
                id="NAV-001",
                route="/(tabs)",
                kind="tabs",
                children=[_nav_node(t, templates, ids) for t in tab_tops],
            )
        )
    root = NavNode(id="NAV-001", route="/", kind="stack", children=groups)
    return NavigationBlock(root=number_navigation(root))


# --------------------------------------------------------------------------- assembly


def _resolve(target: str | None, ids: dict[str, str]) -> str | None:
    if target is None:
        return None
    if target.startswith("@"):
        return ids.get(target[1:])
    return target


# Realistic density: every screen gets recipe-kind boilerplate so a screen block is a few hundred
# tokens (spec 6.2 estimates 400 to 600), which keeps the slice-size tests honest.
_RECIPE_KIND: dict[str, str] = {
    "list": "list",
    "list-with-filters": "list",
    "detail": "detail",
    "detail-with-sticky-cta": "detail",
    "form": "form",
    "auth-form": "form",
    "checkout-form": "form",
}

_ENRICH: dict[str, tuple[dict[str, str], tuple[str, ...], tuple[str, ...], str]] = {
    "list": (
        {
            "error": "We could not load this list. Check your connection and try again.",
            "retry": "Try again",
            "refresh_hint": "Pull down to refresh",
            "end_of_list": "You have reached the end",
            "row_a11y": "Opens the details",
        },
        (
            "Pulling down reloads the list and keeps the scroll position",
            "Scrolling to the end loads the next page of 20 rows",
            "Offline shows the last loaded rows with an offline banner",
            "Every row is at least 44pt tall and reads out its main value",
        ),
        ("offline_banner", "end_of_list_marker"),
        "Rows come from the mock server; keep the scroll position when the shopper comes back "
        "from a detail screen, and never block the screen while a refresh is running.",
    ),
    "detail": (
        {
            "error": "We could not load this item. Please try again.",
            "retry": "Try again",
            "share": "Share",
            "back_a11y": "Go back",
            "image_a11y": "Photo of the item",
        },
        (
            "The back button returns to the previous screen with its scroll position",
            "A failed load shows the error state with a retry button",
            "Images have a text alternative and load progressively",
            "The sticky action stays visible above the keyboard and the safe area",
        ),
        ("back_button", "share_button"),
        "Show placeholders while loading; the main action stays visible while the page scrolls, "
        "and a deep link to this screen must open the same content.",
    ),
    "form": (
        {
            "error": "Something went wrong. Your changes were not saved.",
            "retry": "Try again",
            "required_hint": "This field is required",
            "saving": "Saving...",
            "discard_title": "Discard changes?",
        },
        (
            "Required fields show an inline message and block the submit button",
            "The submit button shows progress and is disabled while saving",
            "Leaving with unsaved changes asks before discarding them",
            "The keyboard never hides the focused field",
        ),
        ("inline_errors", "discard_prompt"),
        "Validate on blur and again on submit; keep what the user typed after an error, and "
        "move focus to the first invalid field.",
    ),
    "other": (
        {
            "error": "Something went wrong. Please try again.",
            "retry": "Try again",
            "offline": "You are offline",
            "back_a11y": "Go back",
            "loading_a11y": "Loading",
        },
        (
            "The screen shows its loading, empty and error states from the recipe",
            "Going offline shows a banner and keeps the last content",
            "Every tap target is at least 44pt and has an accessibility label",
            "Text scales with the system font size without clipping",
        ),
        ("offline_banner", "analytics_hook"),
        "Follow the recipe for loading, empty and error states; record the screen_viewed "
        "analytics event when the screen appears.",
    ),
}


def _screen(
    tpl: _Tpl, ids: dict[str, str], flows: dict[str, str], research_ref: str | None
) -> ScreenBlock:
    extra_texts, extra_checks, extra_sections, note = _ENRICH[_RECIPE_KIND.get(tpl.recipe, "other")]
    actions: list[ScreenAction] = []
    for name, calls, go, on_success in tpl.actions:
        resolved_go = _resolve(go, ids)
        if calls is None and resolved_go is None:
            continue  # its destination is not part of this (smaller) sample
        success = _resolve(on_success, ids)  # "@key" becomes an id (or None when not included)
        actions.append(ScreenAction(name=name, calls=calls, go=resolved_go, on_success=success))
    return ScreenBlock(
        id=ids[tpl.key],
        route=tpl.route,
        surface=tpl.surface,
        recipe=tpl.recipe,
        purpose=tpl.purpose,
        auth=tpl.auth,
        flow=flows.get(tpl.flow) if tpl.flow else None,
        data=[DataBinding(bind=bind, show=list(show)) for bind, show in tpl.data],
        sections=[*tpl.sections, *(s for s in extra_sections if s not in tpl.sections)],
        actions=actions,
        states=dict(tpl.states),
        texts={**extra_texts, **tpl.texts},
        checks=[*tpl.checks, *extra_checks],
        notes=note,
        research_ref=research_ref if tpl.research_ref else None,
    )


_FLOW_DEFS: tuple[tuple[str, str, bool], ...] = (
    ("browse", "Browse and buy", True),
    ("signin", "Sign in or sign up", False),
    ("orders", "Track orders", False),
    ("account", "Manage the account", False),
    ("reviews", "Reviews", False),
)


def _flows(
    templates: Sequence[_Tpl], ids: dict[str, str], flow_ids: dict[str, str]
) -> list[FlowBlock]:
    names = {key: (name, primary) for key, name, primary in _FLOW_DEFS}
    for module in _MODULES:
        names[module.key] = (module.flow_name, False)
    names["admin"] = ("Shop reports", False)

    def members(flow_key: str) -> list[str]:
        if flow_key == "reviews":  # the product page is where reviews start
            wanted = ["product", "reviews", "write_review"]
        elif flow_key == "signin":
            wanted = ["login", "register", "home"]
        elif flow_key == "browse":
            wanted = ["home", "catalog", "product", "cart", "checkout", "order_done"]
        else:
            wanted = [t.key for t in templates if t.flow == flow_key]
        return [k for k in wanted if k in ids]

    out: list[FlowBlock] = []
    for key, flow_id in flow_ids.items():
        keys = members(key)
        if len(keys) < 2:
            continue
        name, primary = names[key]
        out.append(
            FlowBlock(
                id=flow_id,
                name=name,
                primary=primary,
                screens=[ids[k] for k in keys],
                start=ids[keys[0]],
                end=ids[keys[-1]],
                purpose=f"The shopper's journey: {name.lower()}",
            )
        )
    return out


def make_sample_spec(n_screens: int = 12, *, seed: int = 0) -> AppSpec:
    """A deterministic, valid-by-construction commerce-like ``AppSpec`` with ``n_screens`` screens.

    Passes all nine validators with ``make_sample_stack_profile()`` and default ``Limits``.
    ``header.hash`` is set to the rendered hash, so ``parse_spec(render_spec(spec)) == spec``.
    """
    if n_screens < 1:
        raise ValueError("n_screens must be at least 1")
    rng = random.Random(seed)  # noqa: S311 - deterministic sample data, not security
    app_name = rng.choice(["Marketly", "Shopwise", "Cartly", "Bazaar", "Kartly"])
    tone = rng.choice(["friendly", "professional", "playful"])

    templates = _select_templates(n_screens, rng)
    keys = {t.key for t in templates}
    ids = assign_screen_ids(_nav_order(templates), (t.key for t in templates))

    flow_keys = [k for k, _n, _p in _FLOW_DEFS] + [m.key for m in _MODULES] + ["admin"]
    flow_ids = assign_flow_ids(flow_keys)
    flows = _flows(templates, ids, flow_ids)
    kept_flow_ids = {f.id for f in flows}
    flow_for_screen = {k: v for k, v in flow_ids.items() if v in kept_flow_ids}

    entities = _core_entities()
    operations = _core_operations()
    enum_defs = [
        EnumDef(name="ProductStatus", values=["active", "hidden", "sold_out"]),
        EnumDef(name="OrderStatus", values=["placed", "paid", "shipped", "delivered", "cancelled"]),
    ]
    for module in _MODULES:
        if f"{module.key}_list" in keys:
            entities.append(_module_entity(module))
            operations.extend(_module_operations(module))
            for _name, ftype, extra in module.fields:
                if ftype == "enum" and extra in _MODULE_ENUMS:
                    enum_defs.append(EnumDef(name=extra, values=_MODULE_ENUMS[extra]))

    screens = sorted(
        (_screen(t, ids, flow_for_screen, "RA-001") for t in templates), key=lambda s: s.id
    )
    customer_screens = [s.id for s in screens if s.surface == "customer"]
    customer_ops = [o.id for o in operations if "ROLE-admin" not in o.roles]
    research_screen = ids.get("order_detail")

    header = SpecHeader(
        spec_version=1,
        app_id="app_sample",
        brief_hash=sha256_hex("sample-brief"),
        research_note_hash=sha256_hex("sample-research"),
    )
    spec = AppSpec(
        header=header,
        summary=(
            f"{app_name} is a small shop app: browse products, add them to a cart and pay.\n\n"
            "Who uses it: customers (and a shop owner for reports).\n"
            "Mocked for now: payments and email receipts."
        ),
        app=AppBlock(
            name=app_name,
            purpose="Sell products to shoppers on their phones",
            audience="Shoppers who buy a few items a month",
            tone=tone,
        ),
        design=_design(rng),
        navigation=_navigation(templates, ids),
        data=DataBlock(
            entities=entities,
            enums=enum_defs,
            seed_guidance="Use realistic names and prices; keep images on a plain background",
        ),
        api=ApiBlock(
            operations=operations,
            mock_guidance="Return fixtures from memory; latency 150 ms; 5% random errors on checkout",
        ),
        roles=RolesBlock(
            roles=[
                Role(
                    id="ROLE-customer",
                    name="Customer",
                    description="Buys products",
                    surface="customer",
                ),
                Role(
                    id="ROLE-admin",
                    name="Shop owner",
                    description="Sees shop reports",
                    surface="admin",
                ),
            ],
            permissions=[
                Permission(role="ROLE-customer", screens=customer_screens, operations=customer_ops),
                Permission(role="ROLE-admin", screens=["*"], operations=["*"]),
            ],
        ),
        integrations=IntegrationsBlock(
            items=[
                Integration(
                    id="INT-payments",
                    name="Card payments",
                    provider="mock",
                    purpose="Take card payments at checkout (mock until a provider is chosen)",
                    notes="Used by the checkout screen; replace the mock with a provider adapter",
                ),
                Integration(
                    id="INT-email",
                    name="Email receipts",
                    provider="mock",
                    purpose="Send an order receipt by email",
                    source="default",
                ),
            ]
        ),
        behavior=BehaviorBlock(
            deep_links=["/product/[id]", "/orders/[id]"],
            analytics_events=[
                "product_viewed",
                "added_to_cart",
                "checkout_started",
                "order_placed",
            ],
            error_conventions=["Show a plain-language message with a retry button"],
            accessibility=["Tap targets at least 44pt", "Text scales with the system font size"],
        ),
        screens=screens,
        flows=flows,
        build=BuildSequence(steps=[]),
        acceptance=AcceptanceBlock(
            checks=["Every screen renders in light and dark", "A shopper can complete a purchase"]
        ),
        assumptions=AssumptionsBlock(
            assumptions=[
                AssumptionItem(
                    slot_id="login",
                    label="Login",
                    value_text="Email and password",
                    impact="Adds the sign-in and register screens",
                ),
                AssumptionItem(
                    label="Payments",
                    value_text="Mocked",
                    impact="Checkout works without a real payment provider",
                ),
            ],
            research_additions=[
                ResearchAddition(
                    id="RA-001",
                    title="Order tracking timeline",
                    kind="feature",
                    sources=[
                        SourceRef(
                            url="https://example.com/ecommerce-patterns",
                            title="Common e-commerce patterns",
                            domain="example.com",
                            retrieved_at=date(2026, 11, 8),
                        )
                    ],
                    affects=[research_screen] if research_screen else [],
                )
            ],
            research_status="ok",
        ),
        revision=RevisionBlock(version=1),
    )
    spec = spec.model_copy(update={"build": build_sequence(spec)})
    return with_rendered_hash(spec)
