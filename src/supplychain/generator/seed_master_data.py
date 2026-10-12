from decimal import Decimal

from supplychain.db import get_connection


WAREHOUSES = [
    ("Bremen Warehouse", "Bremen"),
    ("Hamburg Warehouse", "Hamburg"),
    ("Berlin Warehouse", "Berlin"),
]

CATEGORIES = [
    "Footwear",
    "Clothing",
    "Accessories",
    "Sports Equipment",
    "Outdoor",
]


def main():
    with get_connection() as connection:
        with connection.cursor() as cursor:
            # Makes repeat runs safe without requiring a schema change.
            # Prevents concurrent runs of this seed script.
            cursor.execute("SELECT pg_advisory_xact_lock(73001);")

            for name, city in WAREHOUSES:
                cursor.execute(
                    """
                    INSERT INTO source.warehouses (name, city)
                    SELECT %s, %s
                    WHERE NOT EXISTS (
                        SELECT 1
                        FROM source.warehouses
                        WHERE name = %s AND city = %s
                    );
                    """,
                    (name, city, name, city),
                )

            for number in range(1, 51):
                category = CATEGORIES[(number - 1) % len(CATEGORIES)]
                price = Decimal("15.00") + Decimal(number) * Decimal("1.25")

                cursor.execute(
                    """
                    INSERT INTO source.products (
                        sku, name, category, unit_price
                    )
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (sku) DO NOTHING;
                    """,
                    (
                        f"SKU-{number:04d}",
                        f"{category} Product {number:02d}",
                        category,
                        price,
                    ),
                )

            cursor.execute("SELECT COUNT(*) FROM source.products;")
            product_count = cursor.fetchone()[0]

            cursor.execute("SELECT COUNT(*) FROM source.warehouses;")
            warehouse_count = cursor.fetchone()[0]

    print("Master data loaded successfully.")
    print(f"Total products: {product_count}")
    print(f"Total warehouses: {warehouse_count}")


if __name__ == "__main__":
    main()