-- One row per calendar month. Amounts are USD before refunds.
-- Aggregate orders to session grain before joining to website_sessions.
WITH RECURSIVE
observed_bounds AS (
    SELECT
        CAST(DATE_FORMAT(MIN(first_at), '%Y-%m-01') AS DATE) AS first_month,
        CAST(DATE_FORMAT(MAX(last_at), '%Y-%m-01') AS DATE) AS last_month
    FROM (
        SELECT MIN(created_at) AS first_at, MAX(created_at) AS last_at
        FROM website_sessions
        UNION ALL
        SELECT MIN(created_at), MAX(created_at)
        FROM orders
    ) source_bounds
),
calendar AS (
    SELECT first_month AS month_start
    FROM observed_bounds
    WHERE first_month IS NOT NULL
    UNION ALL
    SELECT CAST(DATE_ADD(c.month_start, INTERVAL 1 MONTH) AS DATE)
    FROM calendar c
    CROSS JOIN observed_bounds b
    WHERE c.month_start < b.last_month
),
orders_per_session AS (
    SELECT
        website_session_id,
        COUNT(*) AS order_count,
        SUM(price_usd) AS sales_usd
    FROM orders
    GROUP BY website_session_id
),
session_months AS (
    SELECT
        CAST(DATE_FORMAT(s.created_at, '%Y-%m-01') AS DATE) AS month_start,
        COUNT(*) AS sessions,
        SUM(CASE WHEN o.website_session_id IS NOT NULL THEN 1 ELSE 0 END)
            AS converted_sessions,
        COALESCE(SUM(o.order_count), 0) AS session_orders,
        COALESCE(SUM(o.sales_usd), 0) AS session_sales_usd
    FROM website_sessions s
    LEFT JOIN orders_per_session o
        ON s.website_session_id = o.website_session_id
    GROUP BY month_start
),
order_months AS (
    SELECT
        CAST(DATE_FORMAT(created_at, '%Y-%m-01') AS DATE) AS month_start,
        COUNT(*) AS orders,
        SUM(price_usd) AS sales_usd,
        SUM(cogs_usd) AS cogs_usd
    FROM orders
    GROUP BY month_start
)
SELECT
    c.month_start,
    COALESCE(s.sessions, 0) AS sessions,
    COALESCE(s.converted_sessions, 0) AS converted_sessions,
    COALESCE(s.session_orders, 0) AS session_orders,
    COALESCE(s.session_sales_usd, 0) AS session_sales_usd,
    COALESCE(o.orders, 0) AS orders,
    COALESCE(o.sales_usd, 0) AS sales_usd,
    COALESCE(o.cogs_usd, 0) AS cogs_usd
FROM calendar c
LEFT JOIN session_months s ON c.month_start = s.month_start
LEFT JOIN order_months o ON c.month_start = o.month_start
ORDER BY c.month_start;
