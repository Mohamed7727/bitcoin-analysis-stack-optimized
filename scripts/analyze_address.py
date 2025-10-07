#!/usr/bin/env python3
"""
Bitcoin Address Analysis Script
Analyzes address activity using Neo4j graph database
"""

import sys
import os
from neo4j import GraphDatabase
from bitcoinrpc.authproxy import AuthServiceProxy

# Configuration from environment or defaults
NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://localhost:7687')
NEO4J_USER = os.getenv('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'bitcoin123')

BITCOIN_RPC_HOST = os.getenv('BITCOIN_RPC_HOST', 'localhost')
BITCOIN_RPC_PORT = os.getenv('BITCOIN_RPC_PORT', '8332')
BITCOIN_RPC_USER = os.getenv('BITCOIN_RPC_USER', 'btcuser')
BITCOIN_RPC_PASSWORD = os.getenv('BITCOIN_RPC_PASSWORD', 'btcpass')

def analyze_address(address: str):
    """Analyze a Bitcoin address"""
    print(f"\n{'='*60}")
    print(f"Analyzing Address: {address}")
    print(f"{'='*60}\n")

    # Connect to Neo4j
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        with driver.session() as session:
            # Get basic address info
            result = session.run("""
                MATCH (a:Address {address: $address})
                OPTIONAL MATCH (a)<-[r:OUTPUTS_TO]-()
                RETURN a.address as address,
                       a.first_seen as first_seen,
                       sum(r.value) as total_received,
                       count(r) as tx_count
            """, address=address)

            record = result.single()
            if not record or not record['address']:
                print(f"Address not found in database: {address}")
                return

            print(f"First Seen: {record['first_seen']}")
            print(f"Total Received: {record['total_received'] or 0:.8f} BTC")
            print(f"Transaction Count: {record['tx_count'] or 0}")
            print()

            # Find connected addresses
            print("Top Connected Addresses:")
            print("-" * 60)
            result = session.run("""
                MATCH (a1:Address {address: $address})<-[:OUTPUTS_TO]-(t:Transaction)-[:OUTPUTS_TO]->(a2:Address)
                WHERE a1 <> a2
                RETURN a2.address as connected_address,
                       count(t) as connection_count
                ORDER BY connection_count DESC
                LIMIT 10
            """, address=address)

            for i, record in enumerate(result, 1):
                print(f"{i}. {record['connected_address']} ({record['connection_count']} connections)")

            print()

            # Find potential cluster members (common input heuristic)
            print("Potential Cluster Members (Common Input Heuristic):")
            print("-" * 60)
            result = session.run("""
                MATCH (a1:Address {address: $address})<-[:OUTPUTS_TO]-(t1:Transaction)-[:SPENT_IN]->
                      (spend:Transaction)-[:SPENT_IN]->(t2:Transaction)-[:OUTPUTS_TO]->(a2:Address)
                WHERE a1 <> a2
                RETURN DISTINCT a2.address as cluster_address
                LIMIT 10
            """, address=address)

            cluster_members = list(result)
            if cluster_members:
                for i, record in enumerate(cluster_members, 1):
                    print(f"{i}. {record['cluster_address']}")
            else:
                print("No cluster members found")

    finally:
        driver.close()

    print(f"\n{'='*60}\n")

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python analyze_address.py <bitcoin_address>")
        sys.exit(1)

    address = sys.argv[1]
    analyze_address(address)
