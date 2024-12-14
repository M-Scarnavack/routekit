
import csv
from datetime import datetime, timedelta
from hash_table import HashTable
from truck import Truck
from package import Package

# Initialize global variables for distance and address data
distance_data = []
address_data = []

# Load distance data from CSV
def load_distance_data(file_path):
    global distance_data
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        distance_data = [list(map(lambda x: float(x) if x else 0.0, row)) for row in reader]

# Load address data from CSV
def load_address_data(file_path):
    global address_data
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        address_data = [row for row in reader]

# Load package data from CSV and populate the hash table
def load_package_data(file_path, package_hash_table):
    with open(file_path, mode='r') as file:
        reader = csv.reader(file)
        next(reader)  # Skip header row
        for row in reader:
            try:
                print(f"Processing row: {row}")  # Debugging line
                package_id = int(row[0])
                address = row[1]
                city = row[2]
                state = row[3]
                zip_code = row[4]
                deadline = row[5]
                weight = int(row[6].replace(" Kilos", "").strip())
                status = "At Hub"
                package = Package(package_id, address, city, zip_code, deadline, weight, status)
                package_hash_table.insert(package_id, package)
                print(f"Inserted package ID {package_id}")
            except Exception as e:
                print(f"Error processing row {row}: {e}")

# Find distance between two addresses
def get_distance(location1, location2):
    distance = distance_data[location1][location2]
    if distance == 0.0:
        distance = distance_data[location2][location1]
    return distance

# Get address index from address string
def get_address_index(address):
    for index, row in enumerate(address_data):
        if address in row[2]:
            return index
    return -1
# Compute package status for D
def compute_package_status(package, current_time):
    """
    Compute the status of a package at a specific time.

    :param package: Package object
    :param current_time: datetime.timedelta representing the queried time
    :return: Status string ('At Hub', 'En Route', or 'Delivered at <time>')
    """
    if not package.departure_time or current_time < package.departure_time:
        return "At Hub"
    elif package.delivery_time and current_time >= package.delivery_time:
        return f"Delivered at {package.delivery_time}"
    else:
        return "En Route"

# Initialize hash table
package_hash_table = HashTable()

# Load data files
load_distance_data("data/Distance.csv")
load_address_data("data/Address.csv")
load_package_data("data/Package.csv", package_hash_table)

# Validate packages in hash table
for package_id in range(1, 41):  # Assuming 40 packages
    package = package_hash_table.lookup(package_id)
    if not package:
        print(f"Warning: Package ID {package_id} not found in the hash table.")
    else:
        print(f"Package ID {package_id}: {package}")

# Initialize trucks
truck1 = Truck(1, 18, 0, datetime(2024, 12, 12, 8, 0, 0))
truck2 = Truck(2, 18, 0, datetime(2024, 12, 12, 8, 0, 0))
truck3 = Truck(3, 18, 0, datetime(2024, 12, 12, 8, 0, 0))

# Assign packages to trucks
truck1.packages = [pkg for pkg in (package_hash_table.lookup(i) for i in range(1, 17)) if pkg]
truck2.packages = [pkg for pkg in (package_hash_table.lookup(i) for i in range(17, 33)) if pkg]
truck3.packages = [pkg for pkg in (package_hash_table.lookup(i) for i in range(33, 41)) if pkg]

# Debugging: Print assigned packages
print("Truck 1 packages:", [pkg.package_id for pkg in truck1.packages])
print("Truck 2 packages:", [pkg.package_id for pkg in truck2.packages])
print("Truck 3 packages:", [pkg.package_id for pkg in truck3.packages])

# Deliver packages using nearest neighbor algorithm
def deliver_packages(truck):
    current_location = 0
    while truck.packages:
        try:
            next_package = min(
                (pkg for pkg in truck.packages if pkg),  # Filter out None values
                key=lambda package: get_distance(current_location, get_address_index(package.address)),
            )
            truck.packages.remove(next_package)
            distance = get_distance(current_location, get_address_index(next_package.address))
            truck.mileage += distance
            delivery_time = truck.current_time + timedelta(hours=distance / truck.speed)
            truck.current_time = delivery_time
            next_package.status = f"Delivered at {delivery_time}"
            package_hash_table.insert(next_package.package_id, next_package)
            current_location = get_address_index(next_package.address)
        except Exception as e:
            print(f"Error delivering package: {e}")
            break

# Deliver packages
for truck in [truck1, truck2, truck3]:
    deliver_packages(truck)

# Display package statuses
def display_package_status():
    for package_id in range(1, 41):
        package = package_hash_table.lookup(package_id)
        print(package)

# Command line interface
def main():
    print("WGUPS Routing Program")
    while True:
        print("\nMenu:")
        print("1. Lookup a single package at a specific time")
        print("2. View statuses of all packages at a specific time")
        print("3. View total mileage of all trucks")
        print("4. Exit")
        choice = input("Enter your choice: ")

        if choice == "1":
            try:
                package_id = int(input("Enter package ID: "))
                user_time = input("Enter time (HH:MM:SS): ")
                (h, m, s) = user_time.split(":")
                query_time = timedelta(hours=int(h), minutes=int(m), seconds=int(s))
                package = package_hash_table.lookup(package_id)
                if package:
                    status = compute_package_status(package, query_time)
                    print(f"Package ID: {package.package_id}, Address: {package.address}, "
                          f"City: {package.city}, Zip: {package.zip_code}, Deadline: {package.deadline}, "
                          f"Weight: {package.weight} lbs, Status: {status}")
                else:
                    print(f"Package ID {package_id} not found.")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "2":
            try:
                user_time = input("Enter time (HH:MM:SS): ")
                (h, m, s) = user_time.split(":")
                query_time = timedelta(hours=int(h), minutes=int(m), seconds=int(s))
                for package_id in range(1, 41):  # Assuming 40 packages
                    package = package_hash_table.lookup(package_id)
                    if package:
                        status = compute_package_status(package, query_time)
                        print(f"Package ID: {package.package_id}, Address: {package.address}, "
                              f"City: {package.city}, Zip: {package.zip_code}, Deadline: {package.deadline}, "
                              f"Weight: {package.weight} lbs, Status: {status}")
            except Exception as e:
                print(f"Error: {e}")

        elif choice == "3":
            total_mileage = truck1.mileage + truck2.mileage + truck3.mileage
            print(f"Total mileage of all trucks: {total_mileage:.2f} miles")

        elif choice == "4":
            print("Exiting the program.")
            break

        else:
            print("Invalid choice. Please try again.")

if __name__ == "__main__":
    main()
