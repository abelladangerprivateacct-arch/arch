"""Central admin role definitions used by backend authorization and the UI."""

ROLE_DESCRIPTIONS = {
    "super_admin": "Full access, including creating and managing administrators.",
    "operations_admin": "Manage investments, withdrawals, and wallets; read users, referrals, and audit history.",
    "support_admin": "Read user records and respond to support tickets.",
    "auditor": "Read the admin dashboard and audit history only.",
}

ROLE_PERMISSIONS = {
    "super_admin": frozenset({"*"}),
    "operations_admin": frozenset({
        "notifications:manage",
        "dashboard:read",
        "users:read",
        "investments:read",
        "investments:manage",
        "withdrawals:read",
        "withdrawals:manage",
        "wallets:read",
        "wallets:manage",
        "referrals:read",
        "audit:read",
    }),
    "support_admin": frozenset({
        "notifications:manage",
        "dashboard:read",
        "users:read",
        "support:read",
        "support:reply",
    }),
    "auditor": frozenset({
        "notifications:manage",
        "dashboard:read",
        "audit:read",
    }),
}


def role_for_admin(admin):
    """Resolve explicit roles while retaining legacy bootstrap-admin records."""
    role = admin.get("role")
    if role is not None:
        return role if role in ROLE_PERMISSIONS else None
    if admin.get("isSuperAdmin"):
        return "super_admin"
    return "operations_admin"


def permissions_for_role(role):
    return ROLE_PERMISSIONS.get(role, frozenset())


def has_permission(role, permission):
    permissions = permissions_for_role(role)
    return "*" in permissions or permission in permissions
