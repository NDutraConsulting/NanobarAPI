"""Minimal routing/validation/envelope example — run with `uv run nanobar dev examples/basics.py`.

Not the default `nanobar dev` target (that's the root `server.py`, the Nanobar Dashboard) — this
file exists purely to demonstrate the framework's core DX: `@app.get`/`@app.post`, dataclass
request/response validation, and the success/error envelope, without any regression-brick or
eventbus machinery involved.
"""

from dataclasses import dataclass

from starlette.requests import Request

from nanobar_api import NanobarAPI, error, parse

app = NanobarAPI()


@app.get("/ping")
async def ping():
    return {"message": "pong"}


@app.get("/greet/{name}")
async def greet(request: Request):
    return {"message": f"Hello, {request.path_params['name']}!"}


@dataclass
class CreateOrder:
    item: str
    quantity: int


@dataclass
class Order:
    item: str
    quantity: int
    total_cents: int


_PRICE_CENTS = {"widget": 500, "gadget": 1200}


@app.post("/orders", request=CreateOrder, response=Order, summary="Price an order")
async def create_order(request: Request):
    order = parse(CreateOrder, await request.json())
    price = _PRICE_CENTS.get(order.item)
    if price is None:
        return error(f"unknown item {order.item!r}")
    return {"item": order.item, "quantity": order.quantity, "total_cents": price * order.quantity}
