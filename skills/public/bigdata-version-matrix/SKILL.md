---
name: version-matrix
description: Big Data component version compatibility matrix. Load this skill before recommending architecture to ensure all components are version-compatible.
---

# Big Data Component Version Compatibility Matrix

Always cross-reference this matrix when designing a big data deployment architecture.
Incompatible version combinations will cause runtime ClassNotFoundException, NoSuchMethodError,
or silent data corruption.

## Core Rule: Java Version First

| Component       | Supported Java | Notes |
|----------------|---------------|-------|
| Hadoop 2.x      | Java 8 ONLY   | Will NOT work with Java 11+ |
| Hadoop 3.1-3.2  | Java 8        | Java 11 experimental |
| Hadoop 3.3+     | Java 8, 11, 17 | Java 17 from 3.3.5+ |
| Spark 2.x       | Java 8 ONLY   | EOL — avoid for new deployments |
| Spark 3.0-3.3   | Java 8, 11    | |
| Spark 3.4+      | Java 8, 11, 17 | Java 17 recommended |
| Flink 1.15+     | Java 8, 11    | |
| Flink 1.18+     | Java 8, 11, 17 | |
| Kafka 2.x       | Java 8, 11    | |
| Kafka 3.x       | Java 11, 17   | Java 8 deprecated |
| Hive 3.x        | Java 8        | Java 11 experimental in 3.1.3+ |
| HBase 2.4+      | Java 8, 11    | |
| ZooKeeper 3.7+  | Java 8, 11    | |
| Elasticsearch 7 | Java 11+      | Bundled JDK preferred |
| Elasticsearch 8 | Java 17+      | Bundled JDK preferred |

**Decision Rule**: Pick the Java version FIRST, then filter compatible component versions.

## Hadoop ↔ Spark Binding

Spark ships pre-built binaries tied to a specific Hadoop version.

| Spark Version | Compatible Hadoop | Download Suffix |
|--------------|-------------------|-----------------|
| 3.5.x        | 3.3.x             | `-bin-hadoop3`  |
| 3.4.x        | 3.3.x             | `-bin-hadoop3`  |
| 3.3.x        | 3.2.x, 3.3.x      | `-bin-hadoop3`  |
| 3.2.x        | 2.7.x, 3.2.x      | `-bin-hadoop3.2` or `-bin-hadoop2.7` |

**CRITICAL**: Never use a Spark-Hadoop3 binary on a Hadoop 2.x cluster. It will fail with `ClassNotFoundException` on HDFS client classes.

## Hadoop ↔ Hive Binding

| Hive Version | Required Hadoop | Required Tez | Notes |
|-------------|-----------------|-------------|-------|
| 3.1.x       | 3.1.x           | 0.9.x      | Most stable combination |
| 3.1.3       | 3.1.x - 3.3.x   | 0.9.x - 0.10.x | Broader compat |
| 4.0.x       | 3.3.x+          | 0.10.x     | Not yet GA |

## Kafka ↔ ZooKeeper vs KRaft

| Kafka Version | ZooKeeper Mode | KRaft Mode | Recommendation |
|--------------|----------------|------------|----------------|
| < 2.8        | Required       | N/A        | Must use ZK |
| 2.8 - 3.2   | Supported      | Preview    | Use ZK for production |
| 3.3 - 3.5   | Supported      | GA         | KRaft for new clusters, ZK for existing |
| 3.6+         | Deprecated     | Required   | Must use KRaft |

**Decision Rule**: Ask the user if they have an existing ZooKeeper cluster. If yes, check Kafka version compatibility. If new deployment, prefer KRaft (Kafka ≥ 3.3).

## Flink Scala Version

| Flink Version | Scala 2.11 | Scala 2.12 | Notes |
|--------------|------------|------------|-------|
| 1.14.x       | ✅         | ✅         | Last version with Scala 2.11 |
| 1.15+        | ❌         | ✅         | Scala 2.11 dropped |
| 1.18+        | ❌         | ✅         | Scala-free API available |

**Decision Rule**: For new Flink deployments, always use Scala 2.12 builds. If user has existing Scala 2.11 jobs, they must stay on Flink ≤ 1.14.

## Common Pitfalls

1. **Guava version conflicts**: Hadoop and HBase ship different Guava versions. Use `hadoop classpath` to check before adding HBase coprocessors.
2. **Protobuf version conflicts**: Spark 3.x uses Protobuf 3.x, while Hadoop 2.x uses Protobuf 2.5. This causes `com.google.protobuf.InvalidProtocolBufferException`.
3. **Log4j versions**: Hadoop 3.3+ migrated to Log4j2 (reload4j). Spark 3.3+ also uses Log4j2. Mixing Log4j1 and Log4j2 causes logging failures.
4. **Snappy/LZ4 native libraries**: Ensure native compression libraries match the OS architecture (x86_64 vs aarch64/ARM).
