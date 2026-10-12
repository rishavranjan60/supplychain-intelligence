import random
from datetime import timedelta

from supplychain.db import get_connection


def main():
    rng = random.Random(44)

    reasons = [
        "Wrong size",
        "Changed mind",
        "Wrong item ordered",
    ]

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(73004);")

            # This initial seed runs once.
            cursor.execute("SELECT COUNT(*) FROM source.returns;")

            if cursor.fetchone()[0] > 0:
                print("Returns already exist. No new records added.")
                return

            cursor.execute(
                """
                SELECT
                    oi.order_item_id,
                    oi.product_id,
                    oi.quantity,
                    o.warehouse_id,
                    s.delivered_at
                FROM source.order_items AS oi
                JOIN source.orders AS o
                    ON o.order_id = oi.order_id
                JOIN source.shipments AS s
                    ON s.order_id = o.order_id
                WHERE s.status = 'DELIVERED'
                  AND s.delivered_at IS NOT NULL
                ORDER BY oi.order_item_id;
                """
            )
            eligible_items = cursor.fetchall()

            if not eligible_items:
                raise RuntimeError(
                    "No delivered order items found. "
                    "Run seed_shipments first."
                )

            # Return approximately 10% of delivered order lines.
            return_count = max(1, len(eligible_items) // 10)
            selected_items = rng.sample(eligible_items, return_count)

            for item in selected_items:
                (
                    order_item_id,
                    product_id,
                    ordered_quantity,
                    warehouse_id,
                    delivered_at,
                ) = item

                returned_quantity = rng.randint(1, ordered_quantity)
                returned_at = delivered_at + timedelta(
                    days=rng.randint(1, 14)
                )

                cursor.execute(
                    """
                    INSERT INTO source.returns (
                        order_item_id,
                        quantity,
                        returned_at,
                        reason
                    )
                    VALUES (%s, %s, %s, %s);
                    """,
                    (
                        order_item_id,
                        returned_quantity,
                        returned_at,
                        rng.choice(reasons),
                    ),
                )

                cursor.execute(
                    """
                    INSERT INTO source.inventory_movements (
                        warehouse_id,
                        product_id,
                        movement_type,
                        quantity_change,
                        occurred_at
                    )
                    VALUES (%s, %s, 'RETURN', %s, %s);
                    """,
                    (
                        warehouse_id,
                        product_id,
                        returned_quantity,
                        returned_at,
                    ),
                )

    print("Return data loaded successfully.")
    print(f"Returns created: {return_count}")
    print(f"Restock movements created: {return_count}")


if __name__ == "__main__":
    main()