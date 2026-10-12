from supplychain.db import get_connection


TABLES = [
    "products",
    "warehouses",
    "orders",
    "order_items",
    "inventory_movements",
    "shipments",
    "returns",
]

CHECKS = {
    "Orders without order items": """
        SELECT COUNT(*)
        FROM source.orders AS o
        WHERE NOT EXISTS (
            SELECT 1
            FROM source.order_items AS oi
            WHERE oi.order_id = o.order_id
        );
    """,
    "Delivered orders without delivered shipments": """
        SELECT COUNT(*)
        FROM source.orders AS o
        WHERE o.status = 'DELIVERED'
          AND NOT EXISTS (
              SELECT 1
              FROM source.shipments AS s
              WHERE s.order_id = o.order_id
                AND s.status = 'DELIVERED'
                AND s.delivered_at IS NOT NULL
          );
    """,
    "Returns exceeding ordered quantity": """
        SELECT COUNT(*)
        FROM (
            SELECT r.order_item_id
            FROM source.returns AS r
            JOIN source.order_items AS oi
                ON oi.order_item_id = r.order_item_id
            GROUP BY r.order_item_id, oi.quantity
            HAVING SUM(r.quantity) > oi.quantity
        ) AS invalid_returns;
    """,
    "Returns before delivery": """
        SELECT COUNT(*)
        FROM source.returns AS r
        JOIN source.order_items AS oi
            ON oi.order_item_id = r.order_item_id
        JOIN source.shipments AS s
            ON s.order_id = oi.order_id
        WHERE s.delivered_at IS NULL
           OR r.returned_at < s.delivered_at;
    """,
    "Negative current inventory": """
        SELECT COUNT(*)
        FROM (
            SELECT warehouse_id, product_id
            FROM source.inventory_movements
            GROUP BY warehouse_id, product_id
            HAVING SUM(quantity_change) < 0
        ) AS negative_stock;
    """,
    "Dispatch totals not matching shipped quantities": """
        WITH expected AS (
            SELECT
                o.warehouse_id,
                oi.product_id,
                SUM(oi.quantity) AS quantity
            FROM source.order_items AS oi
            JOIN source.orders AS o
                ON o.order_id = oi.order_id
            JOIN source.shipments AS s
                ON s.order_id = o.order_id
            GROUP BY o.warehouse_id, oi.product_id
        ),
        actual AS (
            SELECT
                warehouse_id,
                product_id,
                -SUM(quantity_change) AS quantity
            FROM source.inventory_movements
            WHERE movement_type = 'DISPATCH'
            GROUP BY warehouse_id, product_id
        )
        SELECT COUNT(*)
        FROM expected AS e
        FULL OUTER JOIN actual AS a
            ON a.warehouse_id = e.warehouse_id
           AND a.product_id = e.product_id
        WHERE COALESCE(e.quantity, 0) <> COALESCE(a.quantity, 0);
    """,
    "Restock totals not matching returned quantities": """
        WITH expected AS (
            SELECT
                o.warehouse_id,
                oi.product_id,
                SUM(r.quantity) AS quantity
            FROM source.returns AS r
            JOIN source.order_items AS oi
                ON oi.order_item_id = r.order_item_id
            JOIN source.orders AS o
                ON o.order_id = oi.order_id
            GROUP BY o.warehouse_id, oi.product_id
        ),
        actual AS (
            SELECT
                warehouse_id,
                product_id,
                SUM(quantity_change) AS quantity
            FROM source.inventory_movements
            WHERE movement_type = 'RETURN'
            GROUP BY warehouse_id, product_id
        )
        SELECT COUNT(*)
        FROM expected AS e
        FULL OUTER JOIN actual AS a
            ON a.warehouse_id = e.warehouse_id
           AND a.product_id = e.product_id
        WHERE COALESCE(e.quantity, 0) <> COALESCE(a.quantity, 0);
    """,
}


def main():
    failures = []

    with get_connection() as connection:
        with connection.cursor() as cursor:
            # All checks see the same database snapshot.
            cursor.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY;"
            )

            print("SOURCE TABLE COUNTS")

            for table in TABLES:
                # Table names come only from the fixed list above.
                cursor.execute(f"SELECT COUNT(*) FROM source.{table};")
                count = cursor.fetchone()[0]
                print(f"{table}: {count}")

                if count == 0:
                    failures.append(f"Empty table: {table}")

            print("\nDATA QUALITY CHECKS")

            for name, query in CHECKS.items():
                cursor.execute(query)
                invalid_count = cursor.fetchone()[0]

                result = "PASS" if invalid_count == 0 else "FAIL"
                print(f"{result}: {name} ({invalid_count})")

                if invalid_count > 0:
                    failures.append(name)

    if failures:
        print("\nValidation failed:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print("\nAll source-data checks passed.")


if __name__ == "__main__":
    main()