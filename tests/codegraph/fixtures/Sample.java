package com.coupons.auth;

import java.util.List;
import java.util.Map;
import static com.util.Helpers.log;

public interface Pettable {
    void pet();
}

interface Namable extends Pettable {
    String getName();
}

public abstract class Animal {
    protected int noises = 0;

    public abstract String speak();
}

public class Dog extends Animal implements Namable {
    private String name;

    public Dog(String name) {
        this.name = name;
    }

    @Override
    public String speak() {
        log(name);
        return barkStyle();
    }

    private String barkStyle() {
        return "woof";
    }

    @Override
    public void pet() {
        this.noises += 1;
    }

    @Override
    public String getName() {
        return this.name;
    }

    // Nested inner class to exercise nested-class handling
    public static class Collar {
        public String describe() { return "collar"; }
    }
}

public enum Kind { GOOD, BAD }

public record Point(int x, int y) {
    public int sum() { return x + y; }
}

public class Runner {
    public static void run() {
        Dog d = new Dog("rex");
        d.speak();
        System.out.println("hi");
    }
}
