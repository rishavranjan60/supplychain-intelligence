CREATE SCHEMA IF NOT EXISTS source;

CREATE TABLE source.products (
    product_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE source.warehouses (
    warehouse_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE source.orders (
    order_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    warehouse_id BIGINT NOT NULL
        REFERENCES source.warehouses(warehouse_id),
    ordered_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('PLACED', 'SHIPPED', 'DELIVERED', 'CANCELLED')
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE source.order_items (
    order_item_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES source.orders(order_id),
    line_number INTEGER NOT NULL CHECK (line_number > 0),
    product_id BIGINT NOT NULL REFERENCES source.products(product_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    unit_price NUMERIC(12, 2) NOT NULL CHECK (unit_price >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (order_id, line_number)
);

CREATE TABLE source.inventory_movements (
    movement_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    warehouse_id BIGINT NOT NULL
        REFERENCES source.warehouses(warehouse_id),
    product_id BIGINT NOT NULL REFERENCES source.products(product_id),
    movement_type TEXT NOT NULL CHECK (
        movement_type IN (
            'OPENING_BALANCE',
            'RECEIPT',
            'DISPATCH',
            'RETURN',
            'ADJUSTMENT'
        )
    ),
    quantity_change INTEGER NOT NULL CHECK (quantity_change <> 0),
    occurred_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE source.shipments (
    shipment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL UNIQUE REFERENCES source.orders(order_id),
    shipped_at TIMESTAMPTZ NOT NULL,
    promised_delivery_at TIMESTAMPTZ NOT NULL,
    delivered_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (
        status IN ('IN_TRANSIT', 'DELIVERED', 'LOST')
    ),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (promised_delivery_at >= shipped_at),
    CHECK (delivered_at IS NULL OR delivered_at >= shipped_at)
);

CREATE TABLE source.returns (
    return_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_item_id BIGINT NOT NULL
        REFERENCES source.order_items(order_item_id),
    quantity INTEGER NOT NULL CHECK (quantity > 0),
    returned_at TIMESTAMPTZ NOT NULL,
    reason TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX orders_updated_at_idx
    ON source.orders(updated_at);

CREATE INDEX order_items_updated_at_idx
    ON source.order_items(updated_at);

CREATE INDEX inventory_movements_created_at_idx
    ON source.inventory_movements(created_at);

CREATE INDEX shipments_updated_at_idx
    ON source.shipments(updated_at);

CREATE INDEX returns_updated_at_idx
    ON source.returns(updated_at);