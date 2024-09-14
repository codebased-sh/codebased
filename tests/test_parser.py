from __future__ import annotations

from pathlib import Path

import textwrap

import pytest

from codebased.parser import parse_objects


@pytest.mark.parametrize("file_type", ["ts", "js", "jsx", "tsx"])
def test_javascript_top_level_variable_declarations(file_type):
    source = textwrap.dedent(
        """
        let stringData = "Hello, world!";
        export const numberData = 123;
        const booleanData = true;
        export const nullData = null;
        export let undefinedData = undefined;
        export var objectData = { id: 1, name: 'John', age: 30 };
        var arrayData = [
            { id: 1, name: 'John', age: 30 },
            { id: 2, name: 'Jane', age: 25 },
            { id: 3, name: 'Bob', age: 35 },
        ];
        
        export const hidePII = (datum) => {
            return {id: datum.id};
        };
        function maskPII(datum) {
            return {
                id: datum.id,
                name: datum.name.replace(/./g, '*'),
                age: string(datum.age).replace(/./g, '*'),
            };
        }
        
        export const sanitizedData = hidePII(objectData);
        """
    ).encode()
    file_name = f'src/constants.{file_type}'
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 11
    file_o, string_o, number_o, boolean_o, null_o, undefined_o = objects[:6]
    object_o, array_o, hide_pii_o, mask_pii_o, sanitized_o = objects[6:]
    assert file_o.name == file_name
    assert file_o.kind == 'file'
    assert string_o.name == 'stringData'
    assert string_o.kind == 'definition.constant'
    assert number_o.name == 'numberData'
    assert number_o.kind == 'definition.constant'
    assert boolean_o.name == 'booleanData'
    assert boolean_o.kind == 'definition.constant'
    assert null_o.name == 'nullData'
    assert null_o.kind == 'definition.constant'
    assert undefined_o.name == 'undefinedData'
    assert undefined_o.kind == 'definition.constant'
    assert object_o.name == 'objectData'
    assert object_o.kind == 'definition.constant'
    assert array_o.name == 'arrayData'
    assert array_o.kind == 'definition.constant'
    assert hide_pii_o.name == 'hidePII'
    assert hide_pii_o.kind == 'definition.function'
    assert mask_pii_o.name == 'maskPII'
    assert mask_pii_o.kind == 'definition.function'
    assert sanitized_o.name == 'sanitizedData'
    assert sanitized_o.kind == 'definition.constant'


def test_parse_cxx_header_file():
    file_name = 'src/shapes.h'
    source = textwrap.dedent(
        """
        #ifndef SHAPES_H
        #define SHAPES_H
        
        #include <iostream>
        
        struct Point {
            double x;
            double y;
        };
        
        class Shape {
        public:
            Shape();
            virtual ~Shape();
            virtual double area() = 0;
        };
        
        class Circle : public Shape {
        public:
            Circle(double radius);
            double area() override;
        private:
            double radius_;
        };
        
        class Rectangle : public Shape {
        public:
            Rectangle(double width, double height);
            double area() override;
        private:
            double width_;
            double height_;
        };
        
        #endif
        """
    ).encode()
    source_lines = source.splitlines()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 8

    file, point, shape, shape_area, circle, circle_area, rectangle, rectangle_area = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'cpp'
    assert file.context_before == []
    assert file.context_after == []

    ifndef_start, ifndef_end = source_lines.index(b'#ifndef SHAPES_H'), source_lines.index(b'#endif')

    assert point.name == 'Point'
    assert point.kind == 'definition.struct'
    assert point.context_before == [ifndef_start]
    assert point.context_after == [ifndef_end]

    assert shape.name == 'Shape'
    assert shape.kind == 'definition.class'
    assert shape.context_before == [ifndef_start]
    assert shape.context_after == [ifndef_end]

    shape_start = shape.coordinates[0][0]
    shape_end = shape.coordinates[1][0]

    assert shape_area.name == 'area'
    assert shape_area.kind == 'definition.method'
    assert shape_area.context_before == [ifndef_start, shape_start]
    assert shape_area.context_after == [ifndef_end, shape_end]

    assert circle.name == 'Circle'
    assert circle.kind == 'definition.class'
    assert circle.context_before == [ifndef_start]
    assert circle.context_after == [ifndef_end]

    circle_start = circle.coordinates[0][0]
    circle_end = circle.coordinates[1][0]

    assert circle_area.name == 'area'
    assert circle_area.kind == 'definition.method'
    assert circle_area.context_before == [ifndef_start, circle_start]
    assert circle_area.context_after == [ifndef_end, circle_end]

    assert rectangle.name == 'Rectangle'
    assert rectangle.kind == 'definition.class'
    assert rectangle.context_before == [ifndef_start]
    assert rectangle.context_after == [ifndef_end]

    rectangle_start = rectangle.coordinates[0][0]
    rectangle_end = rectangle.coordinates[1][0]

    assert rectangle_area.name == 'area'
    assert rectangle_area.kind == 'definition.method'
    assert rectangle_area.context_before == [ifndef_start, rectangle_start]
    assert rectangle_area.context_after == [ifndef_end, rectangle_end]


def test_parse_c_header_file():
    # TODO: Properly parse C function declarations.
    # Note: Definitions are correctly parsed.
    file_name = 'src/shapes.h'
    source = textwrap.dedent(
        """
        #ifndef SHAPES_H
        #define SHAPES_H

        #include <stdio.h>

        typedef struct {
            double x;
            double y;
        } Point;

        typedef struct Shape Shape;

        typedef double (*AreaFunc)(const Shape*);

        struct Shape {
            AreaFunc area;
        };

        typedef struct {
            Shape base;
            double radius;
        } Circle;

        typedef struct {
            Shape base;
            double width;
            double height;
        } Rectangle;

        double circle_area(const Shape* shape);
        double rectangle_area(const Shape* shape);

        Circle* create_circle(double radius);
        Rectangle* create_rectangle(double width, double height);

        void destroy_shape(Shape* shape);

        #endif
        """
    ).encode()
    source_lines = source.splitlines()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 6

    file, point, shape_fwd, shape, circle, rectangle = objects

    assert file.name == file_name
    assert file.kind == 'file'
    # This is not ideal, but it's fine.
    assert file.language == 'cpp'
    assert file.context_before == []
    assert file.context_after == []

    ifndef_start, ifndef_end = source_lines.index(b'#ifndef SHAPES_H'), source_lines.index(b'#endif')

    assert point.name == 'Point'
    assert point.kind == 'definition.type'
    assert point.context_before == [ifndef_start]
    assert point.context_after == [ifndef_end]

    assert shape_fwd.name == 'Shape'
    assert shape_fwd.kind == 'definition.type'
    assert shape_fwd.context_before == [ifndef_start]
    assert shape_fwd.context_after == [ifndef_end]

    assert shape.name == 'Shape'
    assert shape.kind == 'definition.struct'
    assert shape.context_before == [ifndef_start]
    assert shape.context_after == [ifndef_end]

    assert circle.name == 'Circle'
    assert circle.kind == 'definition.type'
    assert circle.context_before == [ifndef_start]
    assert circle.context_after == [ifndef_end]

    assert rectangle.name == 'Rectangle'
    assert rectangle.kind == 'definition.type'
    assert rectangle.context_before == [ifndef_start]
    assert rectangle.context_after == [ifndef_end]


def test_parse_rust():
    file_name = 'src/main.rs'
    source = textwrap.dedent(
        """
        #[derive(Debug)]
        pub struct Point {
            x: f64,
            y: f64,
        }
        
        impl Point {
            pub fn new(x: f64, y: f64) -> Self {
                Self { x, y }
            }
        }
        
        fn main() {
            let p = Point::new(1.0, 2.0);
            println!("Hello, world!");
        }
        """
    ).encode()
    source_lines = source.splitlines()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 5

    file, point, impl, function, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'rust'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.struct'

    assert impl.name == 'Point'
    assert impl.kind == 'definition.struct.impl'

    assert function.name == 'new'
    assert function.kind == 'definition.function'
    assert function.context_before == [impl.coordinates[0][0]]
    assert function.context_after == [impl.coordinates[1][0]]

    assert main.name == 'main'
    assert main.kind == 'definition.function'


def test_parse_python():
    file_name = 'src/main.py'
    source = textwrap.dedent(
        """
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y
                
        ORIGIN = Point(0, 0)
        
        def main():
            p = Point(1, 2)
            print("Hello, world!")
        """
    ).encode()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 5

    file, class_, __init__, origin, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'python'
    assert file.context_before == []
    assert file.context_after == []

    assert class_.name == 'Point'
    assert class_.kind == 'definition.class'
    assert class_.context_before == []
    assert class_.context_after == []

    assert __init__.name == '__init__'
    assert __init__.kind == 'definition.function'
    assert __init__.context_before == [class_.coordinates[0][0]]
    assert __init__.context_after == []

    assert origin.name == 'ORIGIN'
    assert origin.kind == 'definition.constant'
    assert origin.context_before == []
    assert origin.context_after == []

    assert main.name == 'main'
    assert main.kind == 'definition.function'
    assert main.context_before == []
    assert main.context_after == []


def test_parse_c_sharp():
    # I know literally nothing about this language.
    # If you're reading this because I made a mistake, please let me know.
    file_name = 'src/Main.cs'
    source = textwrap.dedent(
        """
        public class Point {
            public double X { get; set; }
            public double Y { get; set; }
        }
        
        public static void Main() {
            var p = new Point { X = 1, Y = 2 };
            Console.WriteLine("Hello, world!");
        }
        """
    ).encode()
    source_lines = source.splitlines()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 2

    file, point = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'csharp'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.class'
    assert point.context_before == []


def test_go():
    file_name = 'src/main.go'
    source = textwrap.dedent(
        """
        package main
        
        import "fmt"
        
        type Point struct {
            X float64
            Y float64
        }
        
        func (*Point) Area() float64 {
            return 0
        }
                
        func main() {
            p := Point{X: 1, Y: 2}
            fmt.Println("Hello, world!")
        }
        """
    ).encode()
    objects = parse_objects(
        Path(file_name),
        source
    )
    file, point, area, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'go'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.type'
    assert point.context_before == []
    assert point.context_after == []

    assert area.name == 'Area'
    assert area.kind == 'definition.method'

    assert main.name == 'main'
    assert main.kind == 'definition.function'
    assert main.context_before == []
    assert main.context_after == []


def test_java():
    file_name = 'src/Main.java'
    source = textwrap.dedent(
        """
        public class Point {
            public double x;
            public double y;
            
            public double area() {
                return 0;
            }
        }
        
        public class Main {
            public static void main(String[] args) {
                Point p = new Point();
                System.out.println("Hello, world!");
            }
        }
        """
    ).encode()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 5
    file, point, area, main_class, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'java'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.class'
    assert point.context_before == []
    assert point.context_after == []

    assert area.name == 'area'
    assert area.kind == 'definition.method'
    assert area.context_before == [point.coordinates[0][0]]
    assert area.context_after == [point.coordinates[1][0]]

    assert main_class.name == 'Main'
    assert main_class.kind == 'definition.class'
    assert main_class.context_before == []
    assert main_class.context_after == []

    assert main.name == 'main'
    assert main.kind == 'definition.method'
    assert main.context_before == [main_class.coordinates[0][0]]
    assert main.context_after == [main_class.coordinates[1][0]]


def test_ruby():
    file_name = 'src/main.rb'
    source = textwrap.dedent(
        """
        class Point
            attr_accessor :x, :y
            
            def area
                0
            end
        end
        
        def main
            p = Point.new
            puts "Hello, world!"
        end
        """
    ).encode()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 4
    file, point, area, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'ruby'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.class'
    assert point.context_before == []
    assert point.context_after == []

    assert area.name == 'area'
    assert area.kind == 'definition.method'
    assert area.context_before == [point.coordinates[0][0]]
    assert area.context_after == [point.coordinates[1][0]]

    assert main.name == 'main'
    # In Ruby, all "functions" are methods.
    assert main.kind == 'definition.method'
    assert main.context_before == []
    assert main.context_after == []


def test_php():
    file_name = 'src/main.php'
    source = textwrap.dedent(
        """
        <?php
        
        class Point {
            public double $x;
            public double $y;
            
            public function area(): float {
                return 0;
            }
        }
        
        function main() {
            $p = new Point();
            echo "Hello, world!";
        }
        """
    ).encode()
    objects = parse_objects(
        Path(file_name),
        source
    )
    assert len(objects) == 6

    file, point, x, y, area, main = objects

    assert file.name == file_name
    assert file.kind == 'file'
    assert file.language == 'php'
    assert file.context_before == []
    assert file.context_after == []

    assert point.name == 'Point'
    assert point.kind == 'definition.class'
    assert point.context_before == []
    assert point.context_after == []

    assert x.name == 'x'
    assert x.kind == 'definition.field'
    assert x.context_before == [point.coordinates[0][0]]
    assert x.context_after == [point.coordinates[1][0]]

    assert y.name == 'y'
    assert y.kind == 'definition.field'
    assert y.context_before == [point.coordinates[0][0]]
    assert y.context_after == [point.coordinates[1][0]]

    assert area.name == 'area'
    assert area.kind == 'definition.method'
    assert area.context_before == [point.coordinates[0][0]]
    assert area.context_after == [point.coordinates[1][0]]

    assert main.name == 'main'
    assert main.kind == 'definition.function'
    assert main.context_before == []
    assert main.context_after == []
