// Least-privilege role assignments binding the managed identity to each service.
// Read-only today; add a separate scoped role for each write action the agent earns.

@description('Principal ID (object ID) of the managed identity.')
param principalId string

@description('Azure OpenAI / Cognitive Services account name.')
param openAiAccountName string

@description('AI Search service name.')
param searchServiceName string

@description('Storage account name.')
param storageAccountName string

// Built-in role definition IDs.
var roleCognitiveServicesOpenAiUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
var roleSearchIndexDataReader = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
var roleStorageBlobDataReader = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'

resource openAi 'Microsoft.CognitiveServices/accounts@2024-10-01' existing = {
  name: openAiAccountName
}

resource search 'Microsoft.Search/searchServices@2023-11-01' existing = {
  name: searchServiceName
}

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' existing = {
  name: storageAccountName
}

resource raOpenAi 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: openAi
  name: guid(openAi.id, principalId, roleCognitiveServicesOpenAiUser)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleCognitiveServicesOpenAiUser)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource raSearch 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: search
  name: guid(search.id, principalId, roleSearchIndexDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleSearchIndexDataReader)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource raStorage 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: storage
  name: guid(storage.id, principalId, roleStorageBlobDataReader)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleStorageBlobDataReader)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
