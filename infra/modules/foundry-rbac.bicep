targetScope = 'resourceGroup'

@description('Existing Foundry (Cognitive Services / AIServices) account name.')
param foundryAccountName string

@description('Principal id of the container app managed identity to grant access to.')
param principalId string

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: foundryAccountName
}

// Azure AI Developer, Cognitive Services OpenAI User, Cognitive Services User.
var roleIds = [
  '64702f94-c441-49e6-a78b-ef80e0188fee'
  '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
  'a97b65f3-24c7-4388-baec-2e87135dc908'
]

resource roleAssignments 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for roleId in roleIds: {
    name: guid(account.id, principalId, roleId)
    scope: account
    properties: {
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleId)
      principalId: principalId
      principalType: 'ServicePrincipal'
    }
  }
]
