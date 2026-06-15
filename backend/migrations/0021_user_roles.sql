-- backend/migrations/0020_user_roles.sql
-- App-side authorization layer (spec 2026-06-14-iap-roles-admin-console-design).
-- Google IAP owns the GATE (who can reach the app); this table owns ROLES
-- (what a reached, IAP-verified user may DO). email is the verified identity,
-- stored lowercased. status: 'active' (roled + has signed in), 'invited'
-- (admin pre-assigned, awaiting first sign-in), 'requested' (user asked from
-- the denial page, awaiting an admin). Only 'active'/'invited' admit at the
-- gate; 'requested' is denied until granted.
CREATE TABLE user_roles (
  email        TEXT PRIMARY KEY,
  role         TEXT NOT NULL CHECK (role IN ('admin','member')),
  status       TEXT NOT NULL DEFAULT 'active'
                 CHECK (status IN ('active','invited','requested')),
  display_name TEXT,
  granted_by   TEXT,
  granted_at   TEXT NOT NULL DEFAULT (datetime('now')),
  last_seen_at TEXT
);
