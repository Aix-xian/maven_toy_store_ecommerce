-- All orders are attributed to the month when their website session started.
-- Preserve raw marketing fields so every classification can be audited.
WITH orders_per_session AS (
    SELECT
        website_session_id,
        COUNT(*) AS session_orders,
        SUM(price_usd) AS session_sales_usd,
        SUM(cogs_usd) AS session_cogs_usd
    FROM orders
    GROUP BY website_session_id
)
SELECT
    CAST(DATE_FORMAT(s.created_at, '%Y-%m-01') AS DATE) AS month_start,
    s.utm_source,
    s.utm_campaign,
    s.utm_content,
    s.device_type,
    s.http_referer,
    COUNT(*) AS sessions,
    SUM(CASE WHEN o.website_session_id IS NOT NULL THEN 1 ELSE 0 END)
        AS converted_sessions,
    COALESCE(SUM(o.session_orders), 0) AS session_orders,
    COALESCE(SUM(o.session_sales_usd), 0) AS session_sales_usd,
    COALESCE(SUM(o.session_cogs_usd), 0) AS session_cogs_usd
FROM website_sessions s
LEFT JOIN orders_per_session o
    ON s.website_session_id = o.website_session_id
GROUP BY month_start, s.utm_source, s.utm_campaign, s.utm_content,
         s.device_type, s.http_referer
ORDER BY month_start, s.utm_source, s.utm_campaign, s.utm_content,
         s.device_type, s.http_referer;
