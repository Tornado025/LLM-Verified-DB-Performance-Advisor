CREATE TABLE meta (key text PRIMARY KEY, value text NOT NULL);
CREATE TABLE stores (store_id int PRIMARY KEY, name text NOT NULL, city text NOT NULL,
                     lat double precision, lon double precision);
CREATE TABLE categories (category_id int PRIMARY KEY, name text NOT NULL);
CREATE TABLE products (product_id int PRIMARY KEY, sku text UNIQUE NOT NULL, name text NOT NULL,
                       category_id int NOT NULL REFERENCES categories, brand text,
                       price numeric(10,2) NOT NULL, is_active boolean NOT NULL DEFAULT true,
                       created_at timestamptz NOT NULL);
CREATE TABLE store_stock (store_id int NOT NULL REFERENCES stores,
                          product_id int NOT NULL REFERENCES products,
                          qty int NOT NULL, reserved int NOT NULL DEFAULT 0,
                          updated_at timestamptz NOT NULL, PRIMARY KEY (store_id, product_id));
CREATE TABLE customers (customer_id int PRIMARY KEY, name text NOT NULL, phone text,
                        city text NOT NULL, created_at timestamptz NOT NULL);
CREATE TABLE riders (rider_id int PRIMARY KEY, store_id int NOT NULL REFERENCES stores,
                     name text NOT NULL, status text NOT NULL);
CREATE TABLE orders (order_id bigserial PRIMARY KEY, customer_id int NOT NULL REFERENCES customers,
                     store_id int NOT NULL REFERENCES stores, status text NOT NULL,
                     total numeric(10,2) NOT NULL, payment_mode text NOT NULL,
                     created_at timestamptz NOT NULL, delivered_at timestamptz,
                     rider_id int REFERENCES riders);
CREATE TABLE order_items (order_item_id bigserial PRIMARY KEY,
                          order_id bigint NOT NULL REFERENCES orders,
                          product_id int NOT NULL REFERENCES products, qty int NOT NULL,
                          unit_price numeric(10,2) NOT NULL);
