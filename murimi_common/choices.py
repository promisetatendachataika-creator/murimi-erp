"""
Lists shared by every Murimi module. PROVINCES and the first CROPS are copied
from Murimi Intelligent Hub (accounts/choices.py). Crops added for the ERP are
marked; the Hub and Market must adopt them when the systems are joined
(see INTEGRATION.md).
"""
PROVINCES = [
    "Harare", "Bulawayo", "Manicaland", "Mashonaland Central", "Mashonaland East",
    "Mashonaland West", "Masvingo", "Matabeleland North", "Matabeleland South", "Midlands",
]

HUB_CROPS = ["Maize", "Tobacco", "Tomatoes", "Potatoes", "Sugar beans", "Citrus", "Cotton", "Avocados"]
# Added for mid-sized farms (ERP). Keep "Other" last.
ERP_EXTRA_CROPS = ["Wheat", "Soya beans", "Sorghum", "Groundnuts", "Paprika", "Macadamia", "Blueberries",
                   "Onions", "Cabbages", "Pastures"]
CROPS = HUB_CROPS + ERP_EXTRA_CROPS + ["Other"]

# Currencies used on Zimbabwean farms. ZWG is the ISO code for ZiG (Zimbabwe Gold).
CURRENCIES = [("USD", "US dollar"), ("ZWG", "ZiG (Zimbabwe Gold)"), ("ZAR", "South African rand")]
CURRENCY_CODES = [c for c, _ in CURRENCIES]
REPORTING_CURRENCY = "USD"
