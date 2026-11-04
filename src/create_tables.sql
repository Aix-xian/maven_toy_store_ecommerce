CREATE DATABASE IF NOT EXISTS toy_store_ecommerce
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;

USE toy_store_ecommerce;


-- 1. 商品：每行一个商品
CREATE TABLE IF NOT EXISTS products (
    product_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    product_name VARCHAR(255) NOT NULL,

    PRIMARY KEY (product_id)
) ENGINE = InnoDB;


-- 2. 网站会话：每行一次访问会话
CREATE TABLE IF NOT EXISTS website_sessions (
    website_session_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    user_id INT UNSIGNED NOT NULL,
    is_repeat_session TINYINT UNSIGNED NOT NULL,
    utm_source VARCHAR(100) NULL,
    utm_campaign VARCHAR(100) NULL,
    utm_content VARCHAR(100) NULL,
    device_type VARCHAR(20) NOT NULL,
    http_referer VARCHAR(2048) NULL,

    PRIMARY KEY (website_session_id)
) ENGINE = InnoDB;


-- 3. 页面浏览：每行一次页面浏览
CREATE TABLE IF NOT EXISTS website_pageviews (
    website_pageview_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    website_session_id INT UNSIGNED NOT NULL,
    pageview_url VARCHAR(255) NOT NULL,

    PRIMARY KEY (website_pageview_id),

    CONSTRAINT fk_pageviews_session
        FOREIGN KEY (website_session_id)
        REFERENCES website_sessions (website_session_id)
) ENGINE = InnoDB;


-- 4. 订单：每行一笔订单
CREATE TABLE IF NOT EXISTS orders (
    order_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    website_session_id INT UNSIGNED NOT NULL,
    user_id INT UNSIGNED NOT NULL,
    primary_product_id INT UNSIGNED NOT NULL,
    items_purchased SMALLINT UNSIGNED NOT NULL,
    price_usd DECIMAL(12, 2) NOT NULL,
    cogs_usd DECIMAL(12, 2) NOT NULL,

    PRIMARY KEY (order_id),

    CONSTRAINT fk_orders_session
        FOREIGN KEY (website_session_id)
        REFERENCES website_sessions (website_session_id),

    CONSTRAINT fk_orders_primary_product
        FOREIGN KEY (primary_product_id)
        REFERENCES products (product_id)
) ENGINE = InnoDB;


-- 5. 订单商品明细：每行一条商品明细
CREATE TABLE IF NOT EXISTS order_items (
    order_item_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    order_id INT UNSIGNED NOT NULL,
    product_id INT UNSIGNED NOT NULL,
    is_primary_item TINYINT UNSIGNED NOT NULL,
    price_usd DECIMAL(12, 2) NOT NULL,
    cogs_usd DECIMAL(12, 2) NOT NULL,

    PRIMARY KEY (order_item_id),

    CONSTRAINT fk_order_items_order
        FOREIGN KEY (order_id)
        REFERENCES orders (order_id),

    CONSTRAINT fk_order_items_product
        FOREIGN KEY (product_id)
        REFERENCES products (product_id)
) ENGINE = InnoDB;


-- 6. 商品退款：每行一笔退款记录
CREATE TABLE IF NOT EXISTS order_item_refunds (
    order_item_refund_id INT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL,
    order_item_id INT UNSIGNED NOT NULL,
    order_id INT UNSIGNED NOT NULL,
    refund_amount_usd DECIMAL(12, 2) NOT NULL,

    PRIMARY KEY (order_item_refund_id),

    CONSTRAINT fk_refunds_order_item
        FOREIGN KEY (order_item_id)
        REFERENCES order_items (order_item_id),

    CONSTRAINT fk_refunds_order
        FOREIGN KEY (order_id)
        REFERENCES orders (order_id)
) ENGINE = InnoDB;