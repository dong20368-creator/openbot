"""Bounded generation workers; SQLite owns claims, main thread owns all delivery.

Each thread constructs its own Store, model and read-only source adapter. Same
member ordering is enforced by Store.claim across generation AND delivery.
Inspired by bounded workers in ARQ/PTB, not copied from either implementation.
"""
import threading
from core import Worker

GENERATION_WORKERS=3


class GenerationPool:
    def __init__(self,store_factory,model_factory,source_factory,healthy=lambda:True,
                 stopping=lambda:False,workers=GENERATION_WORKERS,poll=.2,on_ready=lambda:None):
        if workers not in (1,2,3):raise ValueError('Generation concurrency must be 1, 2 or 3')
        self.store_factory,self.model_factory,self.source_factory=store_factory,model_factory,source_factory
        self.healthy,self.stopping,self.poll=healthy,stopping,poll
        self.on_ready=on_ready
        self.stop_event=threading.Event()
        self.available=[threading.Event() for _ in range(workers)]
        self.threads=[threading.Thread(target=self._run,args=(self.available[i],),name=f'answer-generator-{i}',daemon=False) for i in range(workers)]

    def wake(self):
        for available in self.available:available.set()

    def start(self):
        try:
            for thread in self.threads:thread.start()
        except BaseException:
            self.close()
            raise

    def _run(self,available):
        store=None;model=None
        try:
            store=self.store_factory()
            model=self.model_factory()
            if hasattr(model,'warmup'):model.warmup()
            worker=Worker(store,model,self.source_factory(),mode='send',stage='generate')
            while not self.stop_event.is_set() and not self.stopping():
                available.clear()
                try:
                    if self.healthy() and worker.run_one():
                        self.on_ready()
                        continue
                except Exception as exc:
                    store.event(None,'generation_worker_error',error_type=type(exc).__name__)
                    # A model exception is handled by Worker. An unexpected
                    # state/storage error must not keep claiming more jobs.
                    raise
                available.wait(self.poll)
        finally:
            try:
                if model is not None and hasattr(model,'close'):model.close()
            finally:
                if store is not None:store.close()

    def healthy_workers(self):
        return sum(thread.is_alive() for thread in self.threads)

    def close(self):
        self.stop_event.set()
        self.wake()
        # Retain the process singleton lock until every claimed job settles.
        # A stop never sends a generated answer; ready jobs persist for resume.
        for thread in self.threads:
            if thread.ident is not None:thread.join()
