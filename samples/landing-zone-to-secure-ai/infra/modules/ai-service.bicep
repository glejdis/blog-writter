// Azure OpenAI / Foundry account, public network access disabled, reachable only
// over a private endpoint wired into the openai + cognitiveservices DNS zones.

@description('Azure region.')
param location string = resourceGroup().location

@description('Cognitive Services / Azure OpenAI account name.')
param name string

@description('Tags applied to all resources.')
param tags object = {}

@description('Resource ID of the private-endpoint subnet (snet-pep).')
param peSubnetId string

@description('Private DNS zone IDs (from the network module).')
param dnsZoneOpenAiId string
param dnsZoneCognitiveId string

@description('Custom subdomain required for private endpoints + Entra ID auth. Defaults to the account name.')
param customSubDomainName string = name

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    customSubDomainName: customSubDomainName
    publicNetworkAccess: 'Disabled'
    disableLocalAuth: true
    networkAcls: {
      defaultAction: 'Deny'
    }
  }
}

resource pe 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-oai'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'oai-conn'
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
output systemIdentityPrincipalId string = account.identity.principalId
