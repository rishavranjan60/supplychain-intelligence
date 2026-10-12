import random
from datetime import datetime, timedelta, timezone

from supplychain.db import get_connection


def main():
    rng = random.Random(43)
    opening_date = datetime(2025, 12, 31, tzinfo=timezone.utc)

    opening_count = 0
    shipment_count = 0
    dispatch_count = 0

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Prevent overlapping runs of this script.
            cursor.execute("SELECT pg_advisory_xact_lock(73003);")

            cursor.execute(
                "SELECT product_id FROM source.products ORDER BY product_id;"
            )
            products = [row[0] for row in cursor.fetchall()]

            cursor.execute(
                """
                SELECT warehouse_id
                FROM source.warehouses
                ORDER BY warehouse_id;
                """
            )
            warehouses = [row[0] for row in cursor.fetchall()]

            if not products or not warehouses:
                raise RuntimeError("Run seed_master_data first.")

            # Add one opening balance per product and warehouse.
            for warehouse_id in warehouses:
                for product_id in products:
                    cursor.execute(
                        """
                        INSERT INTO source.inventory_movements (
                            warehouse_id,
                            product_id,
                            movement_type,
                            quantity_change,
                            occurred_at
                        )
                        SELECT %s, %s, 'OPENING_BALANCE', 500, %s
                        WHERE NOT EXISTS (
                            SELECT 1
                            FROM source.inventory_movements
                            WHERE warehouse_id = %s
                              AND product_id = %s
                              AND movement_type = 'OPENING_BALANCE'
                        );
                        """,
                        (
                            warehouse_id,
                            product_id,
                            opening_date,
                            warehouse_id,
                            product_id,
                        ),
                    )
                    opening_count += cursor.rowcount

            # Only process placed orders that have no shipment.
            cursor.execute(
                """
                SELECT o.order_id, o.warehouse_id, o.ordered_at
                FROM source.orders AS o
                WHERE o.status = 'PLACED'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM source.shipments AS s
                      WHERE s.order_id = o.order_id
                  )
                ORDER BY o.order_id;
                """
            )
            orders = cursor.fetchall()

            for order_id, warehouse_id, ordered_at in orders:
                cursor.execute(
                    """
                    SELECT product_id, quantity
                    FROM source.order_items
                    WHERE order_id = %s
                    ORDER BY line_number;
                    """,
                    (order_id,),
                )
                items = cursor.fetchall()

                if not items:
                    raise RuntimeError(
                        f"Order {order_id} has no order items."
                    )

                # Verify stock before dispatching the order.
                for product_id, quantity in items:
                    cursor.execute(
                        """
                        SELECT COALESCE(SUM(quantity_change), 0)
                        FROM source.inventory_movements
                        WHERE warehouse_id = %s
                          AND product_id = %s;
                        """,
                        (warehouse_id, product_id),
                    )
                    available_stock = cursor.fetchone()[0]

                    if available_stock < quantity:
                        raise RuntimeError(
                            f"Insufficient stock for order {order_id}, "
                            f"product {product_id}."
                        )

                shipped_at = ordered_at + timedelta(
                    hours=rng.randint(4, 24)
                )
                promised_delivery_at = shipped_at + timedelta(days=3)
                delivered_at = shipped_at + timedelta(
                    days=rng.randint(1, 5)
                )

                cursor.execute(
                    """
                    INSERT INTO source.shipments (
                        order_id,
                        shipped_at,
                        promised_delivery_at,
                        delivered_at,
                        status
                    )
                    VALUES (%s, %s, %s, %s, 'DELIVERED');
                    """,
                    (
                        order_id,
                        shipped_at,
                        promised_delivery_at,
                        delivered_at,
                    ),
                )
                shipment_count += 1

                for product_id, quantity in items:
                    cursor.execute(
                        """
                        INSERT INTO source.inventory_movements (
                            warehouse_id,
                            product_id,
                            movement_type,
                            quantity_change,
                            occurred_at
                        )
                        VALUES (%s, %s, 'DISPATCH', %s, %s);
                        """,
                        (
                            warehouse_id,
                            product_id,
                            -quantity,
                            shipped_at,
                        ),
                    )
                    dispatch_count += 1

                cursor.execute(
                    """
                    UPDATE source.orders
                    SET status = 'DELIVERED',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE order_id = %s;
                    """,
                    (order_id,),
                )

    print("Inventory and shipment data loaded successfully.")
    print(f"Opening balances created: {opening_count}")
    print(f"Shipments created: {shipment_count}")
    print(f"Dispatch movements created: {dispatch_count}")


if __name__ == "__main__":
    main()