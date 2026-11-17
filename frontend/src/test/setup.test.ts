import { createServer } from 'node:http';
import type { AddressInfo } from 'node:net';
import { expect, it, vi } from 'vitest';

it('blocks unhandled test requests before they reach the network', async () => {
  let receivedRequests = 0;
  const listener = createServer((_request, response) => {
    receivedRequests += 1;
    response.end('Unexpected network request');
  });
  await new Promise<void>((resolve, reject) => {
    listener.once('error', reject);
    listener.listen(0, '127.0.0.1', resolve);
  });
  const { port } = listener.address() as AddressInfo;
  const errorLog = vi.spyOn(console, 'error').mockImplementation(() => {});
  const warningLog = vi.spyOn(console, 'warn').mockImplementation(() => {});

  try {
    await expect(
      fetch(`http://127.0.0.1:${port}/__tokenhub_unhandled_test__`),
    ).rejects.toThrow();
    expect(receivedRequests).toBe(0);
  } finally {
    errorLog.mockRestore();
    warningLog.mockRestore();
    await new Promise<void>((resolve, reject) => {
      listener.close((error) => error ? reject(error) : resolve());
    });
  }
});
