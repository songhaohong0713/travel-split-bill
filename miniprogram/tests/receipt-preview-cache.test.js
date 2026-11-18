const test=require('node:test'),assert=require('node:assert/strict')
const cache=require('../services/receipt-preview-cache')

test('temporary cache is limited, image-specific and isolated by account',()=>{
 const removed=[];global.wx={getFileSystemManager:()=>({unlink:o=>{removed.push(o.filePath);o.success?.()}})};cache.clear()
 cache.put('a','t/e','image-1','/tmp/1');assert.equal(cache.get('a','t/e','image-1'),'/tmp/1')
 assert.equal(cache.get('a','t/e','image-2'),'');assert.ok(removed.includes('/tmp/1'))
 cache.put('a','1','v','/tmp/a');cache.put('a','2','v','/tmp/b');cache.put('a','3','v','/tmp/c');cache.put('a','4','v','/tmp/d')
 assert.equal(cache.get('a','1','v'),'');assert.equal(cache.get('a','4','v'),'/tmp/d')
 assert.equal(cache.get('b','4','v'),'');assert.ok(removed.includes('/tmp/d'));cache.clear()
})
