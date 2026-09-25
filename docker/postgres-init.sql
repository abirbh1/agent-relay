-- Scratch database for pytest; its tables are dropped and recreated on every
-- test, so it must never be the database the API container uses.
CREATE DATABASE agent_relay_test OWNER relay;
