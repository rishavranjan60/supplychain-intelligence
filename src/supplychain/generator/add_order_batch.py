import random
from datetime import datetime, timezone

from supplychain.db import get_connection


def main():
    rng = random.Random(45)

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Coordinate with baseline and future incremental loaders.
            cursor.execute("SELECT pg_advisory_xact_lock(73010);")

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
                raise RuntimeError("Master data is missing.")

            # Capture time after acquiring the coordination lock.
            batch_time = datetime.now(timezone.utc)
            item_count = 0

            for _ in range(10):
                warehouse_id = rng.choice(warehouses)

                cursor.execute(
                    """
                    INSERT INTO source.orders (
                        warehouse_id,
                        ordered_at,
                        status,
                        updated_at
                    )
                    VALUES (%s, %s, 'PLACED', %s)
                    RETURNING order_id;
                    """,
                    (warehouse_id, batch_time, batch_time),
                )
                order_id = cursor.fetchone()[0]

                selected_products = rng.sample(
                    products,
                    k=rng.randint(1, min(3, len(products))),
                )

                for line_number, product in enumerate(
                    selected_products, start=1
                ):
                    product_id, unit_price = product

                    cursor.execute(
                        """
                        INSERT INTO source.order_items (
                            order_id,
                            line_number,
                            product_id,
                            quantity,
                            unit_price,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s);
                        """,
                        (
                            order_id,
                            line_number,
                            product_id,
                            rng.randint(1, 3),
                            unit_price,
                            batch_time,
                        ),
                    )
                    item_count += 1

            # Update a product without changing historical order prices.
            changed_product_id = products[0][0]

            cursor.execute(
                """
                UPDATE source.products
                SET unit_price = unit_price + 1.00,
                    updated_at = %s
                WHERE product_id = %s;
                """,
                (batch_time, changed_product_id),
            )

    print("New source batch committed.")
    print("Orders added: 10")
    print(f"Order items added: {item_count}")
    print(f"Product price updated: product {changed_product_id}")
    print(f"Batch timestamp: {batch_time.isoformat()}")


if __name__ == "__main__":
    main()