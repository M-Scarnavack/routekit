class HashTable:
    def __init__(self, size=40):
        """Initialize the hash table with empty buckets."""
        self.size = size
        self.table = [[] for _ in range(size)]

    def _hash(self, key):
        """Hash function to calculate bucket index."""
        return key % self.size

    def insert(self, package_id, package):
        """Insert a package object into the hash table."""
        index = self._hash(package_id)
        for item in self.table[index]:
            if item[0] == package_id:
                item[1] = package
                return
        self.table[index].append([package_id, package])

    def lookup(self, package_id):
        """Look up a package by its ID."""
        index = self._hash(package_id)
        for item in self.table[index]:
            if item[0] == package_id:
                return item[1]
        print(f"Warning: Package ID {package_id} not found in the hash table.")
        return None

    def remove(self, package_id):
        """Remove a package from the hash table."""
        index = self._hash(package_id)
        for item in self.table[index]:
            if item[0] == package_id:
                self.table[index].remove(item)
                return True
        return False
