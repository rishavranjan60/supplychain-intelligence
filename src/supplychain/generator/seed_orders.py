import random
from datetime import datetime, timedelta, timezone

from supplychain.db import get_connection


def main():
    rng = random.Random(42)

    # Fixed dates make this sample repeatable.
    start_date = datetime(2026, 1, 1, tzinfo=timezone.utc)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(73002);")

            cursor.execute("SELECT COUNT(*) FROM source.orders;")
            if cursor.fetchone()[0] > 0:
                print("Orders already exist. No new records added.")
                return

            cursor.execute(
                """
                SELECT product_id, unit_price
                FROM source.products
                ORDER BY product_id;
                """
            )
            products = cursor.fetchall()

            cursor.execute(
                """
                SELECT warehouse_id
                FROM source.warehouses
                ORDER BY warehouse_id;
                """
            )
            warehouses = [row[0] for row in cursor.fetchall()]

            if not products or not warehouses:
                raise RuntimeError(
                    "Products or warehouses are missing. "
                    "Run seed_master_data first."
                )

            item_count = 0

            for _ in range(200):
                warehouse_id = rng.choice(warehouses)

                ordered_at = start_date + timedelta(
                    days=rng.randrange(30),
                    hours=rng.randrange(24),
                    minutes=rng.randrange(60),
                )

                cursor.execute(
                    """
                    INSERT INTO source.orders (
                        warehouse_id, ordered_at, status
                    )
                    VALUES (%s, %s, %s)
                    RETURNING order_id;
                    """,
                    (warehouse_id, ordered_at, "PLACED"),
                )
                order_id = cursor.fetchone()[0]

                selected_products = rng.sample(
                    products,
                    k=rng.randint(1, min(4, len(products))),
                )

                for line_number, product in enumerate(
                    selected_products, start=1
                ):
                    product_id, unit_price = product
                    quantity = rng.randint(1, 5)

                    cursor.execute(
                        """
                        INSERT INTO source.order_items (
                            order_id,
                            line_number,
                            product_id,
                            quantity,
                            unit_price
                        )
                        VALUES (%s, %s, %s, %s, %s);
                        """,
                        (
                            order_id,
                            line_number,
                            product_id,
                            quantity,
                            unit_price,
                        ),
                    )
                    item_count += 1

    print("Order data loaded successfully.")
    print("Orders created: 200")
    print(f"Order items created: {item_count}")


if __name__ == "__main__":
    main()