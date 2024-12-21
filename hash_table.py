# hash_table.py

class HashTable:
    def __init__(self, initial_capacity=20):
        """
        Initialize the hash table with empty buckets.
        Each bucket is a list of [key, value] pairs.
        """
        self.table = [[] for _ in range(initial_capacity)]

    def insert(self, key, value):
        """
        Insert or update a key-value pair in the hash table.
        :param key: The key to insert.
        :param value: The value to be stored, e.g. a Package object.
        """
        bucket_index = hash(key) % len(self.table)
        bucket = self.table[bucket_index]

        # If the key exists, update it
        for pair in bucket:
            if pair[0] == key:
                pair[1] = value
                return True

        # Otherwise, insert a new [key, value] pair
        bucket.append([key, value])
        return True

    def lookup(self, key):
        """
        Return the value associated with the key, or None if not found.
        """
        bucket_index = hash(key) % len(self.table)
        bucket = self.table[bucket_index]

        for pair in bucket:
            if pair[0] == key:
                return pair[1]
        return None

    def remove(self, key):
        """
        Remove a key-value pair by key. Returns True if successful, else False.
        """
        bucket_index = hash(key) % len(self.table)
        bucket = self.table[bucket_index]

        for pair in bucket:
            if pair[0] == key:
                bucket.remove(pair)
                return True
        return False

