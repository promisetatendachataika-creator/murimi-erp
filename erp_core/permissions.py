"""
Who may do what inside one organisation. One table, so it's easy to read and change.

Everyone who belongs to an organisation can VIEW its records. Changing things
needs one of the roles listed here.
"""
from .models import Membership as M

ALL = {M.OWNER, M.MANAGER, M.ACCOUNTANT, M.STOREKEEPER, M.SUPERVISOR, M.VIEWER}

CAN = {
    "view": ALL,
    "manage_members": {M.OWNER},
    "edit_organisation": {M.OWNER, M.MANAGER},
    "edit_farms": {M.OWNER, M.MANAGER},
    "edit_seasons": {M.OWNER, M.MANAGER},
    "edit_rates": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "view_audit": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    # Stage 2: stock and fuel
    "edit_stock_setup": {M.OWNER, M.MANAGER, M.STOREKEEPER},       # items, stores, machines
    "receive_stock": {M.OWNER, M.MANAGER, M.STOREKEEPER},
    "issue_stock": {M.OWNER, M.MANAGER, M.STOREKEEPER, M.SUPERVISOR},  # supervisors record use in the field
    "count_stock": {M.OWNER, M.MANAGER, M.STOREKEEPER},              # transfers and stock takes
    "undo_stock": {M.OWNER, M.MANAGER},
    # Stage 3: labour and payroll. Pay figures are private: supervisors record work but don't see wages.
    "view_pay": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "edit_workers": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "record_work": {M.OWNER, M.MANAGER, M.ACCOUNTANT, M.SUPERVISOR},
    "run_payroll": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "approve_payroll": {M.OWNER, M.ACCOUNTANT},
    "edit_payroll_rules": {M.OWNER, M.ACCOUNTANT},
    # Stage 4: sales, purchases, VAT, harvests, reports
    "view_trade": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "edit_sales": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
    "edit_purchases": {M.OWNER, M.MANAGER, M.ACCOUNTANT, M.STOREKEEPER},
    "void_trade": {M.OWNER, M.ACCOUNTANT},
    "record_harvest": {M.OWNER, M.MANAGER, M.STOREKEEPER, M.SUPERVISOR},
    "view_reports": {M.OWNER, M.MANAGER, M.ACCOUNTANT},
}


def allowed(membership, action):
    return membership is not None and membership.role in CAN[action]


def actions_for(role):
    return sorted(a for a, roles in CAN.items() if role in roles)
