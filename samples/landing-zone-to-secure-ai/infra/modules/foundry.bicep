// Microsoft Foundry account (Cognitive Services kind=AIServices) + a Foundry
// project. This is the model AND agent plane:
//   - model deployments live in the account (Azure OpenAI models *and* other
//     Foundry Models sold by Azure: Grok, Llama, DeepSeek, ...),
//   - Foundry Agent Service runs the agents in the project.
// Public access is disabled, the agent subnet is injected (Standard setup with
// private networking), and the account is reachable only over a private endpoint.
//
// No model is deployed here on purpose: model selection is Part B. The capability
// hosts + BYO connections that complete the standard agent setup come from the
// official foundry-samples template referenced in the infra README.

@description('Azure region.')
param location string = resourceGroup().location

@description('Foundry account name (Cognitive Services kind=AIServices).')
param name string

@description('Foundry project name.')
param projectName string

@description('Tags applied to all resources.')
param tags object = {}

@description('Resource ID of the private-endpoint subnet (snet-pep).')
param peSubnetId string

@description('Resource ID of the delegated agent subnet (snet-agent) for Foundry network injection.')
param agentSubnetId string

@description('Inject the agent subnet into the Foundry account (Standard setup with private networking).')
param networkInjection bool = true

@description('Private DNS zone IDs (from the network module).')
param dnsZoneServicesAiId string
param dnsZoneOpenAiId string
param dnsZoneCognitiveId string

@description('Enforce Entra ID only (no account keys). The official sample uses false; set false if capability-host provisioning requires local auth.')
param disableLocalAuth bool = true

#disable-next-line BCP036
resource account 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    allowProjectManagement: true
    customSubDomainName: name
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: disableLocalAuth
    networkAcls: {
      defaultAction: 'Deny'
      virtualNetworkRules: []
      ipRules: []
      bypass: 'AzureServices'
    }
    networkInjections: networkInjection ? [
      {
        scenario: 'agent'
        subnetArmId: agentSubnetId
        useMicrosoftManagedNetwork: false
      }
    ] : null
  }
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    description: 'Enterprise agent project (Part 1 foundation).'
    displayName: projectName
  }
}

resource pe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-foundry'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'foundry-conn'
        properties: {
          privateLinkServiceId: account.id
          groupIds: [ 'account' ]
        }
      }
    ]
  }
}

resource peDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: pe
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'services-ai'
        properties: {
          privateDnsZoneId: dnsZoneServicesAiId
        }
      }
      {
        name: 'openai'
        properties: {
          privateDnsZoneId: dnsZoneOpenAiId
        }
      }
      {
        name: 'cognitiveservices'
        properties: {
          privateDnsZoneId: dnsZoneCognitiveId
        }
      }
    ]
  }
}

output accountId string = account.id
output accountName string = account.name
output projectName string = project.name
output systemIdentityPrincipalId string = account.identity.principalId
