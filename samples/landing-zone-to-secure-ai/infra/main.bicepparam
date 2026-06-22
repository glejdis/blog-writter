using 'main.bicep'

param env = 'lab'

// First of the current month, format yyyy-MM-dd.
param budgetStartDate = '2026-06-01'

// Allow-list the exact FQDNs your agent's tools need. Everything else is denied.
param allowedFqdns = [
  'api.github.com'
]

param contactEmails = [
  'platform-team@example.com'
]

// true  = Option A: simulated hub + Azure Firewall in one subscription.
// false = Option B: you already operate a hub; peer + egress live there.
param deployFirewall = true
