targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment — used to derive resource names.')
param environmentName string

@minLength(1)
@description('Primary location for all resources.')
param location string

@description('Existing Azure AI Foundry project endpoint.')
param foundryProjectEndpoint string = 'https://blogwriter-dr-gsh.services.ai.azure.com/api/projects/blogwriter-proj'

@description('Resource group that holds the existing Foundry account.')
param foundryResourceGroup string = 'blog-writer-rg'

@description('Existing Foundry account (Cognitive Services / AIServices) name.')
param foundryAccountName string = 'blogwriter-dr-gsh'

@description('Bing grounding connection resource id used by the deep-research stage.')
param bingConnectionId string = '/subscriptions/${subscription().subscriptionId}/resourceGroups/blog-writer-rg/providers/Microsoft.CognitiveServices/accounts/blogwriter-dr-gsh/projects/blogwriter-proj/connections/bing-grounding'

@description('Model deployment name used for every agent role.')
param chatModel string = 'gpt-4o'

@description('Deep-research model deployment name.')
param deepResearchModel string = 'o3-deep-research'

@description('Code sandbox mode: stub (safe for a public endpoint) or local.')
@allowed([
  'stub'
  'local'
])
param sandboxMode string = 'stub'

var resourceSuffix = take(uniqueString(subscription().id, environmentName, location), 6)
var tags = { 'azd-env-name': environmentName }

resource rg 'Microsoft.Resources/resourceGroups@2023-07-01' = {
  name: 'rg-${environmentName}'
  location: location
  tags: tags
}

module app './modules/app.bicep' = {
  name: 'app'
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    tags: tags
    resourceSuffix: resourceSuffix
    foundryProjectEndpoint: foundryProjectEndpoint
    bingConnectionId: bingConnectionId
    chatModel: chatModel
    deepResearchModel: deepResearchModel
    sandboxMode: sandboxMode
  }
}

module foundryRbac './modules/foundry-rbac.bicep' = {
  name: 'foundry-rbac'
  scope: resourceGroup(foundryResourceGroup)
  params: {
    foundryAccountName: foundryAccountName
    principalId: app.outputs.identityPrincipalId
  }
}

output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_LOCATION string = location
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = app.outputs.acrLoginServer
output SERVICE_WEB_NAME string = app.outputs.containerAppName
output WEB_URL string = app.outputs.webUrl
