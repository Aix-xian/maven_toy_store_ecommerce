-- Session-attributed, ordered page-path coverage. Actual orders are independent.
WITH
page_events AS (
    SELECT
        website_session_id, pageview_url,
        ROW_NUMBER() OVER (
            PARTITION BY website_session_id
            ORDER BY created_at, website_pageview_id
        ) AS event_rank,
        CASE
            WHEN pageview_url = '/products' THEN 'products'
            WHEN pageview_url IN (
                '/the-original-mr-fuzzy', '/the-forever-love-bear',
                '/the-birthday-sugar-panda', '/the-hudson-river-mini-bear'
            ) THEN 'detail'
            WHEN pageview_url = '/cart' THEN 'cart'
            WHEN pageview_url = '/shipping' THEN 'shipping'
            WHEN pageview_url IN ('/billing', '/billing-2') THEN 'billing'
            WHEN pageview_url = '/thank-you-for-your-order' THEN 'thank_you'
            ELSE 'other'
        END AS stage
    FROM website_pageviews
),
page_summary AS (
    SELECT
        website_session_id,
        COUNT(*) AS pageview_count,
        MAX(CASE WHEN event_rank = 1 THEN pageview_url END) AS landing_url,
        MAX(CASE WHEN stage = 'products' THEN 1 ELSE 0 END) AS raw_products,
        MAX(CASE WHEN stage = 'detail' THEN 1 ELSE 0 END) AS raw_detail,
        MAX(CASE WHEN stage = 'cart' THEN 1 ELSE 0 END) AS raw_cart,
        MAX(CASE WHEN stage = 'shipping' THEN 1 ELSE 0 END) AS raw_shipping,
        MAX(CASE WHEN stage = 'billing' THEN 1 ELSE 0 END) AS raw_billing,
        MAX(CASE WHEN stage = 'thank_you' THEN 1 ELSE 0 END) AS raw_thank_you
    FROM page_events
    GROUP BY website_session_id
),
path_products AS (
    SELECT website_session_id, MIN(event_rank) AS event_rank
    FROM page_events WHERE stage = 'products'
    GROUP BY website_session_id
),
path_detail AS (
    SELECT e.website_session_id, MIN(e.event_rank) AS event_rank
    FROM page_events e JOIN path_products p
        ON e.website_session_id = p.website_session_id AND e.event_rank > p.event_rank
    WHERE e.stage = 'detail' GROUP BY e.website_session_id
),
path_cart AS (
    SELECT e.website_session_id, MIN(e.event_rank) AS event_rank
    FROM page_events e JOIN path_detail p
        ON e.website_session_id = p.website_session_id AND e.event_rank > p.event_rank
    WHERE e.stage = 'cart' GROUP BY e.website_session_id
),
path_shipping AS (
    SELECT e.website_session_id, MIN(e.event_rank) AS event_rank
    FROM page_events e JOIN path_cart p
        ON e.website_session_id = p.website_session_id AND e.event_rank > p.event_rank
    WHERE e.stage = 'shipping' GROUP BY e.website_session_id
),
path_billing AS (
    SELECT e.website_session_id, MIN(e.event_rank) AS event_rank
    FROM page_events e JOIN path_shipping p
        ON e.website_session_id = p.website_session_id AND e.event_rank > p.event_rank
    WHERE e.stage = 'billing' GROUP BY e.website_session_id
),
path_thank_you AS (
    SELECT e.website_session_id, MIN(e.event_rank) AS event_rank
    FROM page_events e JOIN path_billing p
        ON e.website_session_id = p.website_session_id AND e.event_rank > p.event_rank
    WHERE e.stage = 'thank_you' GROUP BY e.website_session_id
),
orders_per_session AS (
    SELECT website_session_id, COUNT(*) AS session_orders,
           SUM(price_usd) AS session_sales_usd, SUM(cogs_usd) AS session_cogs_usd
    FROM orders GROUP BY website_session_id
)
SELECT
    DATE_FORMAT(s.created_at, '%Y-%m-01') AS month_start,
    s.utm_source, s.utm_campaign, s.utm_content, s.device_type, s.http_referer,
    pv.landing_url,
    COUNT(*) AS sessions,
    SUM(CASE WHEN o.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS converted_sessions,
    COALESCE(SUM(o.session_orders), 0) AS session_orders,
    COALESCE(SUM(o.session_sales_usd), 0) AS session_sales_usd,
    COALESCE(SUM(o.session_cogs_usd), 0) AS session_cogs_usd,
    COALESCE(SUM(pv.pageview_count), 0) AS pageview_count,
    SUM(CASE WHEN pv.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS pageview_sessions,
    SUM(CASE WHEN pv.pageview_count = 1 THEN 1 ELSE 0 END) AS single_page_sessions,
    SUM(CASE WHEN p1.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_products,
    SUM(CASE WHEN p2.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_detail,
    SUM(CASE WHEN p3.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_cart,
    SUM(CASE WHEN p4.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_shipping,
    SUM(CASE WHEN p5.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_billing,
    SUM(CASE WHEN p6.website_session_id IS NOT NULL THEN 1 ELSE 0 END) AS ordered_thank_you,
    SUM(COALESCE(pv.raw_products, 0)) AS raw_products,
    SUM(COALESCE(pv.raw_detail, 0)) AS raw_detail,
    SUM(COALESCE(pv.raw_cart, 0)) AS raw_cart,
    SUM(COALESCE(pv.raw_shipping, 0)) AS raw_shipping,
    SUM(COALESCE(pv.raw_billing, 0)) AS raw_billing,
    SUM(COALESCE(pv.raw_thank_you, 0)) AS raw_thank_you,
    SUM(CASE WHEN o.website_session_id IS NOT NULL AND p6.website_session_id IS NULL
             THEN 1 ELSE 0 END) AS orders_without_ordered_thank_you,
    SUM(CASE WHEN o.website_session_id IS NULL AND p6.website_session_id IS NOT NULL
             THEN 1 ELSE 0 END) AS ordered_thank_you_without_order,
    SUM(CASE WHEN o.website_session_id IS NOT NULL AND COALESCE(pv.raw_thank_you, 0) = 0
             THEN 1 ELSE 0 END) AS orders_without_raw_thank_you,
    SUM(CASE WHEN o.website_session_id IS NULL AND COALESCE(pv.raw_thank_you, 0) = 1
             THEN 1 ELSE 0 END) AS raw_thank_you_without_order
FROM website_sessions s
LEFT JOIN page_summary pv ON s.website_session_id = pv.website_session_id
LEFT JOIN orders_per_session o ON s.website_session_id = o.website_session_id
LEFT JOIN path_products p1 ON s.website_session_id = p1.website_session_id
LEFT JOIN path_detail p2 ON s.website_session_id = p2.website_session_id
LEFT JOIN path_cart p3 ON s.website_session_id = p3.website_session_id
LEFT JOIN path_shipping p4 ON s.website_session_id = p4.website_session_id
LEFT JOIN path_billing p5 ON s.website_session_id = p5.website_session_id
LEFT JOIN path_thank_you p6 ON s.website_session_id = p6.website_session_id
GROUP BY month_start, s.utm_source, s.utm_campaign, s.utm_content,
         s.device_type, s.http_referer, pv.landing_url
ORDER BY month_start, s.utm_source, s.utm_campaign, s.utm_content,
         s.device_type, s.http_referer, pv.landing_url;
