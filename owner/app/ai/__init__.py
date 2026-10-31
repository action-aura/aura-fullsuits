"""AI brain for Aura Owner (2026-10-31). See docs/superpowers/specs/
2026-10-31-ai-brain-design.md for the design and the hard rules R1-R6.

    providers.py     Claude (official SDK) + Gemini (REST) behind one interface
    brain.py         per-purpose provider chains; the R1 privacy filter lives here
    budget.py        spending caps (per licence / per purpose / per staff member)
    prompts.py       system prompts + JSON schemas
    shop_analysis.py POST /api/licensing/v1/ai/shop-analysis (tills)
    leads_rank.py    Leads Finder "Rank with AI"
    staff_help.py    the staff Help button
    routes.py        /assistant/ask and the shared JSON error mapping
    knowledge/       byte-identical copies of the sales docs + owner_screens.md
"""
