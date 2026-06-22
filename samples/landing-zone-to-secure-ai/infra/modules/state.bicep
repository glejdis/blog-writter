// BYO agent-state resources required by the Foundry standard agent setup:
// Cosmos DB (threads + agent metadata), Storage (files), AI Search (vectors),
// Key Vault (secrets). All have public network access disabled and are reachable
// only over private endpoints.

@description('Azure region.')
param location string = resourceGroup().location

@description('Tags applied to all resources.')
param tags object = {}

@description('Resource ID of the private-endpoint subnet (snet-pep).')
param peSubnetId string

@description('Private DNS zone IDs (from the network module).')
param dnsZoneBlobId string
param dnsZoneSearchId string
param dnsZoneCosmosId string
param dnsZoneVaultId string

@description('Globally unique storage account name (<=24 chars, lowercase).')
@maxLength(24)
param storageAccountName string

@description('Globally unique Cosmos DB account name.')
param cosmosAccountName string

@description('Globally unique AI Search service name.')
param searchServiceName string

@description('Globally unique Key Vault name (3-24 chars).')
@maxLength(24)
param keyVaultName string

resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource search 'Microsoft.Search/searchServices@2023-11-01' = {
  name: searchServiceName
  location: location
  tags: tags
  sku: {
    name: 'standard'
  }
  properties: {
    replicaCount: 1
    partitionCount: 1
    publicNetworkAccess: 'disabled'
    disableLocalAuth: true
  }
}

resource cosmos 'Microsoft.DocumentDB/databaseAccounts@2024-11-15' = {
  name: cosmosAccountName
  location: location
  tags: tags
  kind: 'GlobalDocumentDB'
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
      }
    ]
    disableLocalAuth: true
    publicNetworkAccess: 'Disabled'
  }
}

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = {
  name: keyVaultName
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: 'standard'
    }
    tenantId: tenant().tenantId
    enableRbacAuthorization: true
    publicNetworkAccess: 'Disabled'
    networkAcls: {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    }
  }
}

resource peStorage 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-st'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'st-conn'
        properties: {
          privateLinkServiceId: storage.id
          groupIds: [ 'blob' ]
        }
      }
    ]
  }
}

resource peStorageDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peStorage
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'blob'
        properties: {
          privateDnsZoneId: dnsZoneBlobId
        }
      }
    ]
  }
}

resource peSearch 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-srch'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'srch-conn'
        properties: {
          privateLinkServiceId: search.id
          groupIds: [ 'searchService' ]
        }
      }
    ]
  }
}

resource peSearchDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peSearch
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'search'
        properties: {
          privateDnsZoneId: dnsZoneSearchId
        }
      }
    ]
  }
}

resource peCosmos 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-cosmos'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'cosmos-conn'
        properties: {
          privateLinkServiceId: cosmos.id
          groupIds: [ 'Sql' ]
        }
      }
    ]
  }
}

resource peCosmosDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peCosmos
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'cosmos'
        properties: {
          privateDnsZoneId: dnsZoneCosmosId
        }
      }
    ]
  }
}

resource peVault 'Microsoft.Network/privateEndpoints@2023-11-01' = {
  name: 'pep-kv'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: peSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: 'kv-conn'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: [ 'vault' ]
        }
      }
    ]
  }
}

resource peVaultDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2023-11-01' = {
  parent: peVault
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      {
        name: 'vault'
        properties: {
          privateDnsZoneId: dnsZoneVaultId
        }
      }
    ]
  }
}

output storageAccountName string = storage.name
output storageAccountId string = storage.id
output searchServiceName string = search.name
output searchServiceId string = search.id
output cosmosAccountId string = cosmos.id
output keyVaultId string = keyVault.id
