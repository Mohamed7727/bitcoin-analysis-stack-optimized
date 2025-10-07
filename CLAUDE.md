# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Bitcoin Analysis Stack - Optimized Edition is a storage-optimized blockchain analysis platform that uses a **single shared blockchain volume** across all services. This reduces storage from ~2TB to ~1.5TB (25% savings) while maintaining full analysis capabilities through read-only mounts, Redis caching, and batch processing.

## Key Architecture Principle

**Single Source of Truth**: One Bitcoin Core instance maintains the blockchain (`bitcoin_data` volume). All other services mount this volume as **read-only** and build their own indexes/data separately. This is the fundamental difference from the original stack.

## Key Commands

### Service Management
```bash
# Start all services
docker-compose up -d

# Start specific services
docker-compose up -d bitcoin neo4j redis

# Stop all services
docker-compose down

# View logs
docker-compose logs -f bitcoin
docker-compose logs -f btc-importer
docker-compose logs -f graphql

# Restart service
docker-compose restart btc-importer
```

### Bitcoin Core
```bash
# Check blockchain sync status
docker-compose exec bitcoin bitcoin-cli getblockchaininfo

# Get block count
docker-compose exec bitcoin bitcoin-cli getblockcount

# Get specific transaction
docker-compose exec bitcoin bitcoin-cli getrawtransaction <txid> true
```

### Neo4j Database
```bash
# Access Cypher shell
docker-compose exec neo4j cypher-shell -u neo4j -p bitcoin123

# Backup database
docker-compose exec neo4j neo4j-admin dump --database=neo4j --to=/data/neo4j-backup.dump
```

### Redis Cache
```bash
# Access Redis CLI
docker-compose exec redis redis-cli

# Check memory usage
docker-compose exec redis redis-cli INFO memory

# Check cache size
docker-compose exec redis redis-cli DBSIZE

# Flush cache (useful for debugging)
docker-compose exec redis redis-cli FLUSHDB
```

### GraphQL API
```bash
# Health check
curl http://localhost:8000/health

# Shows cache status: {"status": "healthy", "cache": "enabled"}
```

### Verify Shared Volume Setup
```bash
# Check volume mounts
docker volume ls | grep bitcoin
docker inspect bitcoin_node | grep -A 10 Mounts
docker inspect electrs_indexer | grep -A 10 Mounts

# Should show bitcoin_data volume with:
# - bitcoin_node: Mode "rw" (read-write)
# - electrs_indexer: Mode "ro" (read-only)
```

## Architecture & Optimizations

### Shared Volume Architecture
The core optimization is **volume sharing**:

1. **Bitcoin Core** (`bitcoin` service):
   - Mounts `bitcoin_data:/data/.bitcoin` with **RW** (read-write)
   - Only service that writes blockchain data
   - Serves RPC requests to other services

2. **Electrs** (`electrs` service):
   - Mounts `bitcoin_data:/bitcoin:ro` (read-only)
   - Reads blockchain files directly from shared volume
   - Writes its own index to separate `electrs_data` volume
   - Saves ~500GB by not duplicating blockchain

3. **BlockSci** (`blocksci` service):
   - Mounts `bitcoin_data:/data/bitcoin:ro` (read-only)
   - Parses blockchain from shared volume
   - Writes parsed data to separate `blocksci_data` volume

4. **Jupyter** (`jupyter` service):
   - Mounts `bitcoin_data:/data/bitcoin:ro` (read-only)
   - Direct read access to blockchain files for analysis

### Redis Caching Layer

Redis serves two primary functions:

1. **GraphQL API Caching** (DB 1):
   - Caches query results to reduce Bitcoin RPC load
   - TTLs: blockchain info (1min), blocks (10min), transactions (30min), addresses (5min)
   - Cache keys: MD5 hash of query parameters
   - Implementation: `services/graphql/server.py` - `get_cached()` / `set_cached()`

2. **Importer Block Caching** (DB 0):
   - Caches fetched block data to survive restarts
   - TTL: 1 hour
   - Implementation: `services/importer/importer.py` - `get_cached_block()` / `cache_block()`

### Optimized Importer

Key optimizations in `services/importer/importer.py`:

1. **Redis Caching**:
   - Block data cached after fetching
   - Reduces re-fetching on restarts
   - `ENABLE_CACHING=true` environment variable

2. **Batch Processing**:
   - Groups transactions into Neo4j batches
   - `IMPORT_BATCH_SIZE` controls batch size (default 100)
   - Single transaction per batch for atomicity

3. **UNWIND Queries**:
   - Bulk insert outputs using UNWIND
   - Reduces individual INSERT overhead by ~60%
   - See `_import_transaction()` method

4. **Connection Pooling**:
   - `max_connection_pool_size=50` for Neo4j driver
   - Reuses connections across batches

### Optimized GraphQL API

Key features in `services/graphql/server.py`:

1. **Per-Query Caching**:
   - Each resolver checks cache before querying
   - Different TTLs based on data volatility
   - Cache key generation: `cache_key(*args)` function

2. **Health Endpoint**:
   - Reports cache status: enabled/disabled
   - Use for monitoring cache availability

3. **Graceful Cache Degradation**:
   - If Redis unavailable, queries still work (direct to source)
   - Logs warnings but continues operation

## Configuration

### Environment Variables (.env)

Critical settings for optimizations:

```bash
# Importer caching
ENABLE_CACHING=true          # Enable Redis cache for importer
IMPORT_BATCH_SIZE=100        # Blocks per Neo4j transaction

# GraphQL caching
ENABLE_CACHE=true            # Enable Redis cache for API
CACHE_TTL=300                # Default cache TTL (seconds)

# Neo4j optimizations
NEO4J_HEAP_SIZE=4G           # Heap size (increase for better performance)
NEO4J_PAGECACHE=2G           # Page cache (increase for large graphs)
```

### Bitcoin Configuration (config/bitcoin.conf)

Optimizations for shared access:

```ini
# Required for analysis
txindex=1                    # Full transaction index
prune=0                      # No pruning (required for shared access)

# Optimized for multiple readers
dbcache=2048                 # Increase for faster sync
maxmempool=300               # Reduced mempool size
maxorphantx=100              # Reduced orphan tx memory
```

## Volume Structure

Understanding the volume layout:

```
bitcoin_data (600GB)         # SINGLE SHARED VOLUME
├── bitcoin_node (RW)        # Writes blockchain
├── electrs (RO)             # Reads blockchain
├── blocksci (RO)            # Reads blockchain
└── jupyter (RO)             # Reads blockchain

neo4j_data (600GB)           # Separate Neo4j storage
electrs_data (100GB)         # Electrs index only
blocksci_data (200GB)        # BlockSci parsed data
redis_data (2GB)             # Cache storage
importer_state (1MB)         # Import state
importer_cache (10GB)        # Block cache
```

## Neo4j Schema & Optimizations

Same schema as original, with optimizations:

### Batch Import Strategy
```python
# Instead of individual inserts per output:
for output in outputs:
    session.run("MERGE (a:Address {...})")

# Use UNWIND for bulk insert:
session.run("""
    UNWIND $outputs as output
    MERGE (a:Address {address: output.address})
    SET a.first_seen = output.time
""", outputs=output_list)
```

### Memory Settings
```
NEO4J_dbms_memory_transaction_total_max=1G
NEO4J_dbms_tx__log_rotation_retention__policy=1G size
```

## Common Analysis Patterns

Same as original stack - see original CLAUDE.md or README.md for Cypher queries.

## Development Workflows

### Adding Cache to New GraphQL Query

1. Generate cache key:
```python
cache_key_str = cache_key("query_name", param1, param2)
```

2. Check cache:
```python
cached = get_cached(cache_key_str)
if cached:
    return parse_cached_data(cached)
```

3. Query and cache:
```python
result = fetch_data()
set_cached(cache_key_str, json.dumps(result), ttl=600)
return result
```

### Modifying Importer Batch Size

Edit `.env`:
```bash
IMPORT_BATCH_SIZE=500  # Larger batches = faster import, more memory
```

Restart importer:
```bash
docker-compose restart btc-importer
```

### Debugging Cache Issues

```bash
# Check Redis memory
docker-compose exec redis redis-cli INFO memory

# Check cache hit rate (if keys exist)
docker-compose exec redis redis-cli INFO stats

# Clear cache
docker-compose exec redis redis-cli FLUSHDB

# Disable cache for testing
ENABLE_CACHING=false
ENABLE_CACHE=false
docker-compose restart btc-importer graphql
```

### Adding New Read-Only Service

To add another service that reads blockchain:

1. Add to `docker-compose.yml`:
```yaml
  my-service:
    image: my-image
    volumes:
      - bitcoin_data:/bitcoin:ro  # Read-only mount
      - my_service_data:/data     # Service-specific storage
```

2. Ensure service waits for Bitcoin:
```yaml
    depends_on:
      bitcoin:
        condition: service_healthy
```

3. Configure service to use shared path:
```yaml
    environment:
      - BITCOIN_DATA_DIR=/bitcoin/.bitcoin
```

## Troubleshooting

### Electrs "Permission Denied" Error
- **Cause**: Trying to write to read-only volume
- **Fix**: Ensure Electrs writes index to separate volume, not shared bitcoin_data
- **Verify**: Check `ELECTRS_DB_DIR=/data` points to `electrs_data` volume

### Redis Out of Memory
- **Cause**: Cache size exceeded maxmemory limit
- **Fix**: Increase Redis maxmemory in docker-compose.yml
```yaml
command: redis-server --maxmemory 4gb --maxmemory-policy allkeys-lru
```

### Importer Slow After Restart
- **Cause**: Cache cold or disabled
- **Fix**: Ensure `ENABLE_CACHING=true` and Redis is running
- **Verify**: Check Redis connection in importer logs

### GraphQL Returning Stale Data
- **Cause**: Cache TTL too long
- **Fix**: Reduce `CACHE_TTL` or flush cache manually
```bash
docker-compose exec redis redis-cli FLUSHDB
```

### Neo4j Write Performance Issues
- **Cause**: Batch size too small or insufficient heap
- **Fix**:
  - Increase `IMPORT_BATCH_SIZE` to 500-1000
  - Increase `NEO4J_HEAP_SIZE` to 8G or 16G

### Cannot Delete Bitcoin Data
- **Cause**: Multiple containers have volume mounted
- **Fix**: Stop all services before removing volumes
```bash
docker-compose down
docker volume rm bitcoin-analysis-stack-optimized_bitcoin_data
```

## Performance Benchmarks

Expected performance improvements over original stack:

- **Storage**: -25% (500GB saved, from ~2TB to ~1.5TB)
- **Bitcoin RPC calls**: -70% (Redis caching)
- **GraphQL response time**: -50% (cached queries)
- **Neo4j import speed**: +30% (UNWIND batch inserts)

## Important Notes

1. **Read-Only Safety**: Services cannot corrupt blockchain data accidentally
2. **Cache Consistency**: Clear Redis cache after Bitcoin rollbacks/reorgs
3. **Horizontal Scaling**: Multiple analysis services can mount same volume
4. **Backup Strategy**: Only need to backup bitcoin_data once, not per service
5. **Network Access**: All services communicate via Docker network, not shared filesystem

## Security Considerations

- Read-only mounts provide additional safety layer
- Redis cache should not be exposed publicly (port 6379)
- Same password security considerations as original stack
- Cache may contain sensitive address queries - secure Redis accordingly

## Future Optimization Ideas

- Implement cache warming on startup
- Add cache hit/miss metrics to GraphQL
- Explore Neo4j query result caching
- Consider blockchain file deduplication with ZFS/BTRFS
- Add Prometheus metrics for cache performance
