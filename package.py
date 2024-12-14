class Package:
    def __init__(self, package_id, address, city, zip_code, deadline, weight, status):
        self.package_id = package_id
        self.address = address
        self.city = city
        self.zip_code = zip_code
        self.deadline = deadline
        self.weight = weight
        self.status = status
        self.departure_time = None  # Time the package leaves the hub
        self.delivery_time = None  # Time the package is delivered

    def __str__(self):
        return (f"Package ID: {self.package_id}, Address: {self.address}, City: {self.city}, "
                f"Zip: {self.zip_code}, Deadline: {self.deadline}, Weight: {self.weight} lbs, "
                f"Status: {self.status}")
