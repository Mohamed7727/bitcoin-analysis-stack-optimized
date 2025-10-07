#!/usr/bin/env python3
"""
Optimized GraphQL API Server for Bitcoin Blockchain Analysis
Unified interface with Redis caching
"""

import os
import json
import hashlib
import strawberry
from typing import List, Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import GraphQLRouter
from bitcoinrpc.authproxy import AuthServiceProxy
from neo4j import GraphDatabase
import redis
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration
BITCOIN_RPC_HOST = os.getenv('BITCOIN_RPC_HOST', 'bitcoin')
BITCOIN_RPC_PORT = os.getenv('BITCOIN_RPC_PORT', '8332')
BITCOIN_RPC_USER = os.getenv('BITCOIN_RPC_USER', 'btcuser')
BITCOIN_RPC_PASSWORD = os.getenv('BITCOIN_RPC_PASSWORD', 'btcpass')

NEO4J_URI = os.getenv('NEO4J_URI', 'bolt://neo4j:7687')
NEO4J_USER = os.getenv('NEO4J_USER', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', 'bitcoin123')

REDIS_HOST = os.getenv('REDIS_HOST', 'redis')
REDIS_PORT = int(os.getenv('REDIS_PORT', '6379'))

ENABLE_CACHE = os.getenv('ENABLE_CACHE', 'true').lower() == 'true'
CACHE_TTL = int(os.getenv('CACHE_TTL', '300'))  # 5 minutes default

# Initialize connections
btc_rpc_url = f"http://{BITCOIN_RPC_USER}:{BITCOIN_RPC_PASSWORD}@{BITCOIN_RPC_HOST}:{BITCOIN_RPC_PORT}"
btc = AuthServiceProxy(btc_rpc_url)
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

# Redis cache
redis_client = None
if ENABLE_CACHE:
    try:
        redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, db=1, decode_responses=True)
        redis_client.ping()
        logger.info("Redis cache connected")
    except Exception as e:
        logger.warning(f"Redis cache unavailable: {e}")
        redis_client = None

def cache_key(*args) -> str:
    """Generate cache key from arguments"""
    key_str = ":".join(str(arg) for arg in args)
    return hashlib.md5(key_str.encode()).hexdigest()

def get_cached(key: str) -> Optional[str]:
    """Get value from cache"""
    if not redis_client:
        return None
    try:
        return redis_client.get(f"gql:{key}")
    except Exception as e:
        logger.warning(f"Cache read error: {e}")
        return None

def set_cached(key: str, value: str, ttl: int = CACHE_TTL):
    """Set value in cache"""
    if not redis_client:
        return
    try:
        redis_client.setex(f"gql:{key}", ttl, value)
    except Exception as e:
        logger.warning(f"Cache write error: {e}")

# GraphQL Types
@strawberry.type
class BlockInfo:
    hash: str
    height: int
    time: int
    size: int
    tx_count: int
    confirmations: Optional[int] = None

@strawberry.type
class TransactionOutput:
    address: str
    value: float
    n: int

@strawberry.type
class TransactionInput:
    txid: Optional[str] = None
    vout: Optional[int] = None
    coinbase: Optional[str] = None

@strawberry.type
class Transaction:
    txid: str
    size: int
    time: Optional[int] = None
    block_hash: Optional[str] = None
    inputs: List[TransactionInput]
    outputs: List[TransactionOutput]

@strawberry.type
class AddressInfo:
    address: str
    balance: float
    tx_count: int
    first_seen: Optional[int] = None

@strawberry.type
class AddressRelation:
    from_address: str
    to_address: str
    total_amount: float
    tx_count: int

@strawberry.type
class NetworkStats:
    blocks: int
    difficulty: float
    hashrate: float
    chain: str
    size_on_disk: int

# Queries
@strawberry.type
class Query:
    @strawberry.field
    def blockchain_info(self) -> NetworkStats:
        """Get blockchain statistics"""
        cache_key_str = cache_key("blockchain_info")
        cached = get_cached(cache_key_str)

        if cached:
            data = json.loads(cached)
            return NetworkStats(**data)

        info = btc.getblockchaininfo()
        result = NetworkStats(
            blocks=info['blocks'],
            difficulty=info['difficulty'],
            hashrate=0,  # Would need additional calculation
            chain=info['chain'],
            size_on_disk=info['size_on_disk']
        )

        # Cache for 1 minute
        set_cached(cache_key_str, json.dumps({
            'blocks': result.blocks,
            'difficulty': result.difficulty,
            'hashrate': result.hashrate,
            'chain': result.chain,
            'size_on_disk': result.size_on_disk
        }), ttl=60)

        return result

    @strawberry.field
    def block(self, height: Optional[int] = None, hash: Optional[str] = None) -> Optional[BlockInfo]:
        """Get block by height or hash"""
        cache_key_str = cache_key("block", height or hash)
        cached = get_cached(cache_key_str)

        if cached:
            data = json.loads(cached)
            return BlockInfo(**data)

        try:
            if height is not None:
                block_hash = btc.getblockhash(height)
            elif hash is not None:
                block_hash = hash
            else:
                return None

            block = btc.getblock(block_hash, 1)
            result = BlockInfo(
                hash=block['hash'],
                height=block['height'],
                time=block['time'],
                size=block['size'],
                tx_count=len(block['tx']),
                confirmations=block.get('confirmations')
            )

            # Cache for 10 minutes
            set_cached(cache_key_str, json.dumps({
                'hash': result.hash,
                'height': result.height,
                'time': result.time,
                'size': result.size,
                'tx_count': result.tx_count,
                'confirmations': result.confirmations
            }), ttl=600)

            return result
        except Exception as e:
            logger.error(f"Error fetching block: {e}")
            return None

    @strawberry.field
    def transaction(self, txid: str) -> Optional[Transaction]:
        """Get transaction by txid"""
        cache_key_str = cache_key("transaction", txid)
        cached = get_cached(cache_key_str)

        if cached:
            data = json.loads(cached)
            return Transaction(
                txid=data['txid'],
                size=data['size'],
                time=data.get('time'),
                block_hash=data.get('block_hash'),
                inputs=[TransactionInput(**inp) for inp in data['inputs']],
                outputs=[TransactionOutput(**out) for out in data['outputs']]
            )

        try:
            tx = btc.getrawtransaction(txid, True)

            inputs = []
            for vin in tx.get('vin', []):
                if 'coinbase' in vin:
                    inputs.append(TransactionInput(coinbase=vin['coinbase']))
                else:
                    inputs.append(TransactionInput(
                        txid=vin.get('txid'),
                        vout=vin.get('vout')
                    ))

            outputs = []
            for vout in tx.get('vout', []):
                addresses = vout.get('scriptPubKey', {}).get('addresses', [])
                for addr in addresses:
                    outputs.append(TransactionOutput(
                        address=addr,
                        value=vout['value'],
                        n=vout['n']
                    ))

            result = Transaction(
                txid=tx['txid'],
                size=tx['size'],
                time=tx.get('time'),
                block_hash=tx.get('blockhash'),
                inputs=inputs,
                outputs=outputs
            )

            # Cache for 30 minutes
            set_cached(cache_key_str, json.dumps({
                'txid': result.txid,
                'size': result.size,
                'time': result.time,
                'block_hash': result.block_hash,
                'inputs': [{'txid': i.txid, 'vout': i.vout, 'coinbase': i.coinbase} for i in inputs],
                'outputs': [{'address': o.address, 'value': o.value, 'n': o.n} for o in outputs]
            }), ttl=1800)

            return result
        except Exception as e:
            logger.error(f"Error fetching transaction: {e}")
            return None

    @strawberry.field
    def address_info(self, address: str) -> Optional[AddressInfo]:
        """Get address information from Neo4j graph"""
        cache_key_str = cache_key("address_info", address)
        cached = get_cached(cache_key_str)

        if cached:
            data = json.loads(cached)
            return AddressInfo(**data)

        try:
            with neo4j_driver.session() as session:
                result = session.run("""
                    MATCH (a:Address {address: $address})
                    OPTIONAL MATCH (a)<-[r:OUTPUTS_TO]-()
                    RETURN a.address as address,
                           a.first_seen as first_seen,
                           sum(r.value) as balance,
                           count(r) as tx_count
                """, address=address)

                record = result.single()
                if not record:
                    return None

                addr_info = AddressInfo(
                    address=record['address'],
                    balance=record['balance'] or 0,
                    tx_count=record['tx_count'] or 0,
                    first_seen=record['first_seen']
                )

                # Cache for 5 minutes
                set_cached(cache_key_str, json.dumps({
                    'address': addr_info.address,
                    'balance': addr_info.balance,
                    'tx_count': addr_info.tx_count,
                    'first_seen': addr_info.first_seen
                }), ttl=300)

                return addr_info
        except Exception as e:
            logger.error(f"Error fetching address info: {e}")
            return None

    @strawberry.field
    def address_connections(self, address: str, limit: int = 10) -> List[AddressRelation]:
        """Find addresses connected to the given address"""
        cache_key_str = cache_key("address_connections", address, limit)
        cached = get_cached(cache_key_str)

        if cached:
            data = json.loads(cached)
            return [AddressRelation(**rel) for rel in data]

        try:
            with neo4j_driver.session() as session:
                result = session.run("""
                    MATCH (a1:Address {address: $address})<-[r1:OUTPUTS_TO]-(t:Transaction)-[r2:OUTPUTS_TO]->(a2:Address)
                    WHERE a1 <> a2
                    RETURN a1.address as from_address,
                           a2.address as to_address,
                           sum(r2.value) as total_amount,
                           count(DISTINCT t) as tx_count
                    ORDER BY tx_count DESC
                    LIMIT $limit
                """, address=address, limit=limit)

                relations = []
                for record in result:
                    relations.append(AddressRelation(
                        from_address=record['from_address'],
                        to_address=record['to_address'],
                        total_amount=record['total_amount'] or 0,
                        tx_count=record['tx_count']
                    ))

                # Cache for 10 minutes
                set_cached(cache_key_str, json.dumps([{
                    'from_address': r.from_address,
                    'to_address': r.to_address,
                    'total_amount': r.total_amount,
                    'tx_count': r.tx_count
                } for r in relations]), ttl=600)

                return relations
        except Exception as e:
            logger.error(f"Error fetching address connections: {e}")
            return []

    @strawberry.field
    def transaction_path(self, from_address: str, to_address: str, max_hops: int = 5) -> List[str]:
        """Find shortest transaction path between two addresses"""
        cache_key_str = cache_key("transaction_path", from_address, to_address, max_hops)
        cached = get_cached(cache_key_str)

        if cached:
            return json.loads(cached)

        try:
            with neo4j_driver.session() as session:
                result = session.run("""
                    MATCH path = shortestPath(
                        (a1:Address {address: $from_address})-[:OUTPUTS_TO|SPENT_IN*..%d]-(a2:Address {address: $to_address})
                    )
                    RETURN [node in nodes(path) | node.address] as addresses
                """ % (max_hops * 2), from_address=from_address, to_address=to_address)

                record = result.single()
                if record:
                    path = [addr for addr in record['addresses'] if addr]
                    # Cache for 30 minutes
                    set_cached(cache_key_str, json.dumps(path), ttl=1800)
                    return path
                return []
        except Exception as e:
            logger.error(f"Error finding transaction path: {e}")
            return []

# Create schema
schema = strawberry.Schema(query=Query)

# Create FastAPI app
app = FastAPI(title="Bitcoin Analysis GraphQL API (Optimized)", version="2.0.0")

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add GraphQL route
graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")

# Health check endpoint
@app.get("/health")
async def health_check():
    try:
        # Check Bitcoin RPC
        btc.getblockcount()

        # Check Neo4j
        with neo4j_driver.session() as session:
            session.run("RETURN 1")

        # Check Redis
        cache_status = "enabled" if redis_client and redis_client.ping() else "disabled"

        return {
            "status": "healthy",
            "cache": cache_status
        }
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}

@app.get("/")
async def root():
    return {
        "message": "Bitcoin Analysis GraphQL API (Optimized)",
        "version": "2.0.0",
        "features": ["shared blockchain volume", "redis caching", "batch processing"],
        "graphql_endpoint": "/graphql",
        "health_endpoint": "/health"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv('GRAPHQL_PORT', 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
