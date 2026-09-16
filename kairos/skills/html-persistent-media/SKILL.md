---
name: "html-persistent-media"
description: "Add persistent video/image media storage to browser-based HTML applications using IndexedDB. Covers blob storage setup, clipboard paste of media files, drag-and-drop, in-app video playback, and export"
priority: 0.5
imported-from: "agents"
source-path: "C:\\Users\\leohu\\.agents\\skills\\software-development\\html-persistent-media\\SKILL.md"
---
# HTML Persistent Media Storage (IndexedDB)

Add video/image support to HTML apps with **IndexedDB** for persistent blob storage (hundreds of MB), unlike `localStorage` (5MB limit).

## When to Use

- An HTML app (canvas, whiteboard, editor) needs to **accept pasted/dropped video files**
- Media must **survive page refresh** but `localStorage` is too small
- Need **export/import** that includes embedded media data
- Need **inline video playback** inside HTML elements

## IndexedDB Setup

### Database & CRUD Functions

```javascript
var DB=null, DB_NAME='AppMedia', DB_VER=1;

function dbOpen(){
  return new Promise(function(rj){
    var req=indexedDB.open(DB_NAME, DB_VER);
    req.onupgradeneeded=function(e){
      var db=e.target.result;
      if(!db.objectStoreNames.contains('blobs'))
        db.createObjectStore('blobs');
    };
    req.onsuccess=function(e){ DB=e.target.result; rj(); };
    req.onerror=function(e){ console.warn('IndexedDB failed:',e); rj(); };
  });
}

function dbPut(key, data){
  return new Promise(function(rj){
    if(!DB){ rj(null); return; }
    var tx=DB.transaction('blobs','readwrite'),
        store=tx.objectStore('blobs');
    store.put(data, key).onsuccess=function(){ rj(key); };
  });
}

function dbGet(key){
  return new Promise(function(rj){
    if(!DB){ rj(null); return; }
    var tx=DB.transaction('blobs','readonly'),
        store=tx.objectStore('blobs');
    store.get(key).onsuccess=function(e){ rj(e.target.result); };
  });
}

function dbDel(key){
  return new Promise(function(rj){
    if(!DB){ rj(); return; }
    var tx=DB.transaction('blobs','readwrite'),
        store=tx.objectStore('blobs');
    store.delete(key).onsuccess=function(){ rj(); };
  });
}

var bid=0;
function dbStoreBlob(blob){
  return new Promise(function(rj){
    bid++;
    var key='b_'+bid+'_'+Date.now();
    dbPut(key, blob).then(function(){ rj(key); });
  });
}

dbOpen();
```

## Clipboard Paste: Video Files

```javascript
document.addEventListener('paste', function(e){
  e.preventDefault();
  for(var i=0;i<e.clipboardData.items.length;i++){
    var item=e.clipboardData.items[i];
    
    // Paste image file
    if(item.type.indexOf('image/')===0){
      var file=item.getAsFile(), reader=new FileReader();
      reader.onload=function(ev){
        // create node with media=ev.target.result (data URL)
      };
      reader.readAsDataURL(file);
      return;
    }
    
    // Paste video file → store in IndexedDB
    if(item.type.indexOf('video/')===0){
      var file=item.getAsFile();
      dbStoreBlob(file).then(function(blobKey){
        // create node with media=blobKey (key reference, not data URL)
        // video will be loaded from IndexedDB when rendered
      });
      return;
    }
  }
  
  // Handle video URLs
  var text=e.clipboardData.getData('text');
  if(/(youtube|\.mp4|\.webm|\.mov)/i.test(text) && /^https?:/i.test(text)){
    // create node with media=text (URL reference)
  }
});
```

## Drag-and-Drop: Video Files

```javascript
vp.addEventListener('dragover', function(e){ e.preventDefault(); });

vp.addEventListener('drop', function(e){
  e.preventDefault();
  if(!e.dataTransfer.files.length) return;
  var file=e.dataTransfer.files[0];
  
  if(file.type.indexOf('video/')===0){
    dbStoreBlob(file).then(function(blobKey){
      // create node with media=blobKey
    });
  } else if(file.type.indexOf('image/')===0){
    var reader=new FileReader();
    reader.onload=function(ev){
      // create node with media=ev.target.result (data URL)
    };
    reader.readAsDataURL(file);
  }
});
```

## Video Player Rendering

```javascript
function loadVideo(nodeId, mediaRef){
  var container=document.getElementById('vd_'+nodeId);
  if(!container) return;
  
  // Loading from IndexedDB
  if(mediaRef.indexOf('blob:')===0){
    dbGet(mediaRef).then(function(blob){
      if(!blob){
        container.innerHTML='<div>Video data unavailable</div>';
        return;
      }
      var url=URL.createObjectURL(blob);
      container.innerHTML='<video style="width:100%;height:100%" controls playsinline>'+
        '<source src="'+url+'"></video>';
    });
  }
  // Loading from URL/file path
  else {
    var vurl=mediaRef;
    if(/^[a-zA-Z]:\\/i.test(vurl))
      vurl='file:///'+vurl.replace(/\\/g,'/');
    container.innerHTML='<video style="width:100%;height:100%" controls playsinline>'+
      '<source src="'+vurl+'"></video>';
  }
}
```

## Export/Import with Blobs

### Export: Include blob data in JSON

```javascript
function exportWithBlobs(data){
  var blobKeys=[];
  // Collect all blob keys from your data structure
  data.N.forEach(function(n){
    if(n.media && n.media.indexOf('blob:')===0)
      blobKeys.push(n.media);
  });
  
  if(blobKeys.length===0){
    // Export JSON directly (no blobs)
    downloadJSON(data);
    return;
  }
  
  // Fetch all blobs and attach to export data
  var promises=blobKeys.map(function(k){
    return dbGet(k).then(function(b){ return {key:k, data:b}; });
  });
  Promise.all(promises).then(function(blobs){
    data._blobs=blobs;
    downloadJSON(data);
  });
}
```

### Import: Restore blobs to IndexedDB

```javascript
function importWithBlobs(data){
  var blobs=data._blobs || [];
  if(blobs.length===0){
    renderData(data);
    return;
  }
  
  var idx=0;
  function importNext(){
    if(idx>=blobs.length){ renderData(data); return; }
    var bd=blobs[idx]; idx++;
    dbPut(bd.key, bd.data).then(function(){ importNext(); });
  }
  importNext();
}
```

## Cleanup on Delete

When deleting a media node, remove its blob from IndexedDB:

```javascript
function deleteNode(id){
  // Save media reference before removing from array
  var mediaRef=null;
  for(var j=0;j<items.length;j++)
    if(items[j].id===id){ mediaRef=items[j].media; break; }
  
  // Remove from DOM and data
  // ...
  
  // Clean up IndexedDB blob
  if(mediaRef && mediaRef.indexOf('blob:')===0)
    dbDel(mediaRef);
}
```

## Pitfalls

| Pitfall | Solution |
|---------|----------|
| `indexedDB.open()` is async — blobs not ready immediately | Call `dbOpen()` on page load, check `DB!==null` before operations |
| Video blob may be **hundreds of MB** — export JSON will be huge | Warn user before export with large blobs |
| `URL.createObjectURL()` creates an ephemeral URL — not persistent | Object URLs are temporary; always keep the blob in IndexedDB |
| Internet Explorer / legacy browsers don't support IndexedDB | Target modern Chrome/Firefox/Edge/Safari |
| User clears browser storage → IndexedDB is wiped | Mention this in app UI |
| **`file://` URLs in Chrome do NOT persist IndexedDB across sessions** — data is cleared on browser close | Use `localStorage` (data URL) for persistent data when app is served via `file://`. IndexedDB only works persistently with `http://`/`https://` origins |
| `readAsDataURL()` for images is fine (small files), but **don't use for videos** | Use IndexedDB for video, data URL for images |
| `.env` file not parsed in browser context | API keys in browser tools must use config.yaml or hardcoded values |
