export function GET() {
  return new Response('ok\n', { status: 200, headers: { 'content-type': 'text/plain' } });
}
