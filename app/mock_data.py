"""Mock business context used by the gather_context tool.

This stands in for a real CRM/knowledge base. Anything returned from here is
simulated, not supplied by the user, and tools must label it as such.
"""

MOCK_BUSINESS_CONTEXT: dict = {
    "company_name": "Nimbus Retail Co.",
    "industry": "e-commerce (home goods)",
    "team_size": "45 employees",
    "existing_tools": ["Zendesk for support tickets", "Shopify for storefront"],
    "notable_constraint": "Support team currently handles ~500 tickets/week manually",
}
