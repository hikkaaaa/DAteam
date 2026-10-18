// Persistent storage in this browser only (IndexedDB): analyzed sessions with their video files,
// results, manual annotations and categories. Nothing is sent to any server.
// If IndexedDB is unavailable (private mode, blocked), everything keeps working in memory.

const DB_NAME = "cash-control";
const DB_VERSION = 1;
let dbPromise = null;

function openDb() {
  dbPromise ||= new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);
    request.onupgradeneeded = () => {
      request.result.createObjectStore("sessions", { keyPath: "id" });
      request.result.createObjectStore("categories", { keyPath: "id" });
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  return dbPromise;
}

async function dbRequest(store, mode, makeRequest) {
  try {
    const db = await openDb();
    return await new Promise((resolve, reject) => {
      const request = makeRequest(db.transaction(store, mode).objectStore(store));
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  } catch (err) {
    console.warn(`Storage (${store}) unavailable:`, err);
    return mode === "readonly" ? [] : null;
  }
}

const dbGetAll = (store) => dbRequest(store, "readonly", (s) => s.getAll());
const dbPut = (store, value) => dbRequest(store, "readwrite", (s) => s.put(value));
const dbDelete = (store, id) => dbRequest(store, "readwrite", (s) => s.delete(id));

const newId = () => (crypto.randomUUID?.() || `${Date.now()}-${Math.random().toString(16).slice(2)}`);
