// The user-assigned managed identity, created BEFORE any AI service exists.
// Use one identity per agent so actions can be attributed and revoked per agent.

@description('Azure region.')
param location string = resourceGroup().location

@description('Managed identity name, e.g. id-ai-app-lab.')
param name string

@description('Tags applied to all resources.')
param tags object = {}

resource uami 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: name
  location: location
  tags: tags
}

output principalId string = uami.properties.principalId
output clientId string = uami.properties.clientId
output id string = uami.id
output name string = uami.name
