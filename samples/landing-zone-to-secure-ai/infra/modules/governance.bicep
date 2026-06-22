// Governance baseline: a Log Analytics workspace for diagnostics + a monthly
// budget with an 80% actual-cost alert.

@description('Azure region.')
param location string = resourceGroup().location

@description('Short environment/name suffix, e.g. lab.')
param env string

@description('Tags applied to all resources.')
param tags object = {}

@description('Monthly budget amount in your billing currency.')
param budgetAmount int = 200

@description('Budget start date, first of a month, format yyyy-MM-dd.')
param budgetStartDate string

@description('Email addresses notified at 80% of budget.')
param contactEmails array = [
  'platform-team@example.com'
]

resource law 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: 'log-ai-${env}'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: 30
  }
}

resource budget 'Microsoft.Consumption/budgets@2023-11-01' = {
  name: 'budget-ai-${env}'
  properties: {
    category: 'Cost'
    amount: budgetAmount
    timeGrain: 'Monthly'
    timePeriod: {
      startDate: budgetStartDate
    }
    notifications: {
      actual80: {
        enabled: true
        operator: 'GreaterThanOrEqualTo'
        threshold: 80
        thresholdType: 'Actual'
        contactEmails: contactEmails
      }
    }
  }
}

output logAnalyticsWorkspaceId string = law.id
