// main.bicep — composes Part A (the platform foundation) from the blog
// "From Azure Landing Zone to Secure AI (Part 1)".
//
// Deploy:
//   az group create -n rg-ai-lab -l swedencentral
//   az deployment group create -g rg-ai-lab -f main.bicep -p main.bicepparam
//
// Order mirrors the lab: network -> firewall -> identity -> state -> ai service
// -> least-privilege RBAC -> governance. Nothing here deploys a model; that is
// Part B (the agent).

targetScope = 'resourceGroup'

@description('Azure region for all resources.')
param location string = resourceGroup().location

@description('Short environment/name suffix, e.g. lab.')
param env string = 'lab'

@description('FQDNs the agent is allowed to reach for tool calls.')
param allowedFqdns array = [
  'api.github.com'
]

@description('Deploy the simulated hub + Azure Firewall (Option A). Set false for Option B (existing hub).')
param deployFirewall bool = true

@description('Budget start date, first of a month, format yyyy-MM-dd.')
param budgetStartDate string

@description('Emails notified at 80% of the monthly budget.')
param contactEmails array = [
  'platform-team@example.com'
]

var tags = {
  workload: 'ai-agent'
  owner: 'platform-team'
  environment: env
  dataClass: 'internal'
}

// Globally-unique-ish names derived from the resource group id.
var suffix = take(uniqueString(resourceGroup().id), 8)
var storageAccountName = take(toLower('stai${env}${suffix}'), 24)
var cosmosAccountName = toLower('cosmos-ai-${env}-${suffix}')
var searchServiceName = toLower('srch-ai-${env}-${suffix}')
var keyVaultName = take(toLower('kv-ai-${env}${suffix}'), 24)
var openAiAccountName = toLower('oai-ai-${env}-${suffix}')

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    location: location
    env: env
    tags: tags
    deployHub: deployFirewall
  }
}

module firewall 'modules/firewall.bicep' = if (deployFirewall) {
  name: 'firewall'
  params: {
    location: location
    env: env
    tags: tags
    firewallSubnetId: network.outputs.firewallSubnetId
    allowedFqdns: allowedFqdns
  }
}

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    location: location
    name: 'id-ai-app-${env}'
    tags: tags
  }
}

module state 'modules/state.bicep' = {
  name: 'state'
  params: {
    location: location
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    dnsZoneBlobId: network.outputs.dnsZoneBlobId
    dnsZoneSearchId: network.outputs.dnsZoneSearchId
    dnsZoneCosmosId: network.outputs.dnsZoneCosmosId
    dnsZoneVaultId: network.outputs.dnsZoneVaultId
    storageAccountName: storageAccountName
    cosmosAccountName: cosmosAccountName
    searchServiceName: searchServiceName
    keyVaultName: keyVaultName
  }
}

module ai 'modules/ai-service.bicep' = {
  name: 'ai-service'
  params: {
    location: location
    name: openAiAccountName
    tags: tags
    peSubnetId: network.outputs.peSubnetId
    dnsZoneOpenAiId: network.outputs.dnsZoneOpenAiId
    dnsZoneCognitiveId: network.outputs.dnsZoneCognitiveId
  }
}

module rbac 'modules/rbac.bicep' = {
  name: 'rbac'
  params: {
    principalId: identity.outputs.principalId
    openAiAccountName: ai.outputs.accountName
    searchServiceName: state.outputs.searchServiceName
    storageAccountName: state.outputs.storageAccountName
  }
}

module governance 'modules/governance.bicep' = {
  name: 'governance'
  params: {
    location: location
    env: env
    tags: tags
    budgetStartDate: budgetStartDate
    contactEmails: contactEmails
  }
}

@description('Handoff artifact #1: the managed identity the agent authenticates with.')
output managedIdentityPrincipalId string = identity.outputs.principalId
output managedIdentityClientId string = identity.outputs.clientId

@description('Handoff artifact #2: the private AI service the agent calls.')
output openAiAccountId string = ai.outputs.accountId
output spokeVnetId string = network.outputs.spokeVnetId
output agentSubnetId string = network.outputs.agentSubnetId
