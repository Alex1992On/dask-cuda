import pytest

import os

from distributed.utils_test import gen_cluster, loop, gen_test
from distributed.worker import Worker
from distributed import Client, get_worker, wait
from dask_cuda import LocalCUDACluster
from dask_cuda.device_host_file import DeviceHostFile
from zict.file import _safe_key as safe_key

import dask
import dask.array as da
import cupy
import cudf


def assert_device_host_file_size(
    dhf, total_bytes, device_chunk_overhead=0, serialized_chunk_overhead=1024
):
    byte_sum = dhf.device_buffer.fast.total_weight + dhf.host_buffer.fast.total_weight
    file_path = [os.path.join(dhf.disk.directory, safe_key(k)) for k in dhf.disk.keys()]
    file_size = [os.path.getsize(f) for f in file_path]
    byte_sum += sum(file_size)

    # Allow up to chunk_overhead bytes overhead per chunk on disk
    device_overhead = len(dhf.device) * device_chunk_overhead
    host_overhead = len(dhf.host) * serialized_chunk_overhead
    disk_overhead = len(dhf.disk) * serialized_chunk_overhead
    assert (
        byte_sum >= total_bytes
        and byte_sum <= total_bytes + device_overhead + host_overhead + disk_overhead
    )


def worker_assert(total_size, device_chunk_overhead, serialized_chunk_overhead):
    assert_device_host_file_size(
        get_worker().data, total_size, device_chunk_overhead, serialized_chunk_overhead
    )


@pytest.mark.parametrize(
    "params",
    [
        {
            "device_memory_limit": 1e9,
            "memory_limit": 4e9,
            "host_target": 0.6,
            "host_spill": 0.7,
            "spills_to_disk": False,
        },
        {
            "device_memory_limit": 1e9,
            "memory_limit": 2e9,
            "host_target": 0.6,
            "host_spill": 0.6,
            "spills_to_disk": True,
        },
    ],
)
def test_cupy_device_spill(params):
    @gen_cluster(
        client=True,
        nthreads=[("127.0.0.1", 1)],
        Worker=Worker,
        timeout=300,
        worker_kwargs={
            "memory_limit": params["memory_limit"],
            "data": DeviceHostFile(
                device_memory_limit=params["device_memory_limit"],
                memory_limit=params["memory_limit"],
            ),
        },
        config={
            "distributed.worker.memory.target": params["host_target"],
            "distributed.worker.memory.spill": params["host_spill"],
        },
    )
    def test_device_spill(client, scheduler, worker):
        rs = da.random.RandomState(RandomState=cupy.random.RandomState)
        x = rs.random(int(250e6), chunks=10e6)
        yield wait(x)

        xx = x.persist()
        yield wait(xx)

        # Allow up to 1024 bytes overhead per chunk serialized
        yield client.run(worker_assert, x.nbytes, 0, 1024)

        y = client.compute(x.sum())
        res = yield y

        assert (abs(res / x.size) - 0.5) < 1e-3

        yield client.run(worker_assert, x.nbytes, 0, 1024)
        host_chunks = yield client.run(lambda: len(get_worker().data.host))
        disk_chunks = yield client.run(lambda: len(get_worker().data.disk))
        for hc, dc in zip(host_chunks.values(), disk_chunks.values()):
            if params["spills_to_disk"]:
                assert dc > 0
            else:
                assert hc > 0
                assert dc == 0

    test_device_spill()


@pytest.mark.parametrize(
    "params",
    [
        {
            "device_memory_limit": 1e9,
            "memory_limit": 4e9,
            "host_target": 0.6,
            "host_spill": 0.7,
            "spills_to_disk": False,
        },
        {
            "device_memory_limit": 1e9,
            "memory_limit": 2e9,
            "host_target": 0.6,
            "host_spill": 0.6,
            "spills_to_disk": True,
        },
    ],
)
@pytest.mark.asyncio
async def test_cupy_cluster_device_spill(loop, params):
    async with LocalCUDACluster(
        1,
        scheduler_port=0,
        processes=True,
        silence_logs=False,
        dashboard_address=None,
        asynchronous=True,
        death_timeout=300,
        device_memory_limit=params["device_memory_limit"],
        memory_limit=params["memory_limit"],
        memory_target_fraction=params["host_target"],
        memory_spill_fraction=params["host_spill"],
    ) as cluster:
        async with Client(cluster, asynchronous=True) as client:

            rs = da.random.RandomState(RandomState=cupy.random.RandomState)
            x = rs.random(int(250e6), chunks=10e6)
            await wait(x)

            xx = x.persist()
            await wait(xx)

            # Allow up to 1024 bytes overhead per chunk serialized
            await client.run(worker_assert, x.nbytes, 0, 1024)

            y = client.compute(x.sum())
            res = await y

            assert (abs(res / x.size) - 0.5) < 1e-3

            await client.run(worker_assert, x.nbytes, 0, 1024)
            host_chunks = await client.run(lambda: len(get_worker().data.host))
            disk_chunks = await client.run(lambda: len(get_worker().data.disk))
            for hc, dc in zip(host_chunks.values(), disk_chunks.values()):
                if params["spills_to_disk"]:
                    assert dc > 0
                else:
                    assert hc > 0
                    assert dc == 0


@pytest.mark.parametrize(
    "params",
    [
        {
            "device_memory_limit": 1e9,
            "memory_limit": 4e9,
            "host_target": 0.6,
            "host_spill": 0.7,
            "spills_to_disk": False,
        },
        {
            "device_memory_limit": 1e9,
            "memory_limit": 2e9,
            "host_target": 0.6,
            "host_spill": 0.6,
            "spills_to_disk": True,
        },
    ],
)
def test_cudf_device_spill(params):
    @gen_cluster(
        client=True,
        ncores=[("127.0.0.1", 1)],
        Worker=Worker,
        timeout=300,
        worker_kwargs={
            "memory_limit": params["memory_limit"],
            "data": DeviceHostFile(
                device_memory_limit=params["device_memory_limit"],
                memory_limit=params["memory_limit"],
            ),
        },
        config={
            "distributed.comm.timeouts.connect": "20s",
            "distributed.worker.memory.target": params["host_target"],
            "distributed.worker.memory.spill": params["host_spill"],
        },
    )
    def test_device_spill(client, scheduler, worker):

        cdf = dask.datasets.timeseries(
            dtypes={"x": int, "y": float}, freq="30ms"
        ).map_partitions(cudf.from_pandas)

        sizes = yield client.compute(cdf.map_partitions(lambda df: df.__sizeof__()))
        sizes = sizes.tolist()
        nbytes = sum(sizes)
        part_index_nbytes = (yield client.compute(cdf.partitions[0].index)).__sizeof__()

        cdf2 = cdf.persist()
        yield wait(cdf2)

        del cdf

        yield client.run(worker_assert, nbytes, 32, 2048 + part_index_nbytes)

        host_chunks = yield client.run(lambda: len(get_worker().data.host))
        disk_chunks = yield client.run(lambda: len(get_worker().data.disk))
        for hc, dc in zip(host_chunks.values(), disk_chunks.values()):
            if params["spills_to_disk"]:
                assert dc > 0
            else:
                assert hc > 0
                assert dc == 0

        del cdf2

        yield client.run(worker_assert, 0, 0, 0)

    test_device_spill()


@pytest.mark.parametrize(
    "params",
    [
        {
            "device_memory_limit": 1e9,
            "memory_limit": 4e9,
            "host_target": 0.6,
            "host_spill": 0.7,
            "spills_to_disk": False,
        },
        {
            "device_memory_limit": 1e9,
            "memory_limit": 2e9,
            "host_target": 0.6,
            "host_spill": 0.6,
            "spills_to_disk": True,
        },
    ],
)
@pytest.mark.asyncio
async def test_cudf_cluster_device_spill(loop, params):
    async with LocalCUDACluster(
        1,
        device_memory_limit=params["device_memory_limit"],
        memory_limit=params["memory_limit"],
        memory_target_fraction=params["host_target"],
        memory_spill_fraction=params["host_spill"],
        death_timeout=300,
        asynchronous=True,
    ) as cluster:
        async with Client(cluster, asynchronous=True) as client:

            cdf = dask.datasets.timeseries(
                dtypes={"x": int, "y": float}, freq="30ms"
            ).map_partitions(cudf.from_pandas)

            sizes = await client.compute(cdf.map_partitions(lambda df: df.__sizeof__()))
            sizes = sizes.tolist()
            nbytes = sum(sizes)
            part_index_nbytes = (
                await client.compute(cdf.partitions[0].index)
            ).__sizeof__()

            cdf2 = cdf.persist()
            await wait(cdf2)

            del cdf

            await client.run(worker_assert, nbytes, 32, 2048 + part_index_nbytes)

            host_chunks = await client.run(lambda: len(get_worker().data.host))
            disk_chunks = await client.run(lambda: len(get_worker().data.disk))
            for hc, dc in zip(host_chunks.values(), disk_chunks.values()):
                if params["spills_to_disk"]:
                    assert dc > 0
                else:
                    assert hc > 0
                    assert dc == 0

            del cdf2

            await client.run(worker_assert, 0, 0, 0)